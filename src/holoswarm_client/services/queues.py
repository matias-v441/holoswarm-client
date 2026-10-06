"""Queue operations and synchronization with the bridge.

All queue API traffic goes through QueueService. Network work runs as coroutines on the API loop;
results come back to the GUI thread as closures through a SimpleQueue drained by process_events(),
so stores and widgets are only touched on the GUI thread.

A queue is created once with all of its missions (from the QueueDraft), then submitted (the fleet
manager stages its first step on the robots) and started. While it is not submitted it can be
edited: the draft loads a copy and upload() replaces the stored queue with it (same id). The bridge
keeps the queues, so nothing is persisted here.
"""

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from queue import Empty, SimpleQueue
from typing import Any, Mapping

import websockets

from holoswarm_client.data.execution import ExecutionStore, QueueExecution, SyncState
from holoswarm_client.data.queue_draft import QueueDraft
from holoswarm_client.iroc.client import ApiError, IROCClient, TransportError

RECONNECT_MIN = 1.0
RECONNECT_MAX = 10.0
RECONCILE_PERIOD = 60.0  # [s] safety-net snapshot even when events look consistent

CREATING = "<new queue>"  # pending key while a queue is being created

type MessageCallback = Callable[[str, bool], None]
type CreatedCallback = Callable[[QueueExecution], None]


class QueueService:

    def __init__(self, client: IROCClient, api_loop: asyncio.AbstractEventLoop, executions: ExecutionStore, draft: QueueDraft,
                 workspace: str | None = None) -> None:
        self.client = client
        self.workspace = workspace  # new queues are stored in it (None: the bridge's default)
        self.api_loop = api_loop
        self.executions = executions
        self.draft = draft
        self.schedulers: list[str] = []
        self.pending: set[str] = set()  # queue ids with a request in flight, CREATING while creating (GUI thread)

        self._updates: SimpleQueue[Callable[[], None]] = SimpleQueue()
        self._message_callbacks: list[MessageCallback] = []
        self._created_callbacks: list[CreatedCallback] = []
        self._listen_future: Future | None = None
        self._refresh_pending = False

    # | ----------------------- lifecycle (GUI thread) ----------------------- |

    def start(self) -> None:
        if self._listen_future is None or self._listen_future.done():
            self._listen_future = asyncio.run_coroutine_threadsafe(self._listen(), self.api_loop)
        self.refresh_schedulers()

    def stop(self) -> None:
        if self._listen_future is not None and not self._listen_future.done():
            self._listen_future.cancel()

    def process_events(self) -> None:
        while True:
            try:
                update = self._updates.get_nowait()
            except Empty:
                break
            update()
        self.executions.notify()
        self.draft.notify()

    def on_created(self, callback: CreatedCallback) -> None:
        """A queue was created on the bridge (its stored state, maybe before the store's event)."""
        self._created_callbacks.append(callback)

    def on_message(self, callback: MessageCallback) -> None:
        """Operator-facing result of commands: (text, success)."""
        self._message_callbacks.append(callback)

    # | ----------------------- commands (GUI thread) ----------------------- |

    def refresh(self) -> None:
        if self._refresh_pending:
            return
        self._refresh_pending = True
        asyncio.run_coroutine_threadsafe(self._refresh(), self.api_loop)

    def refresh_schedulers(self) -> None:
        async def fetch() -> None:
            try:
                schedulers = await self.client.schedulers()
            except (ApiError, TransportError):
                return  # the fleet manager may be down; the defaults are offered meanwhile

            def apply() -> None:
                self.schedulers = schedulers
                self.executions.mark_changed()

            self._post(apply)

        asyncio.run_coroutine_threadsafe(fetch(), self.api_loop)

    def create(self) -> Future | None:
        """Create a queue from the whole (new queue) draft. The draft keeps its missions until the bridge has the queue."""
        if self.draft.editing:
            return None
        missions = self.draft.missions
        if not missions:
            self._emit_message("Add missions to the new queue first.", False)
            return None
        if CREATING in self.pending:
            return None
        if not self._sendable("Queue not created"):
            return None

        settings = self.draft.settings
        name = settings.name.strip()
        queue_id = f"q-{uuid.uuid4().hex[:12]}"
        wire = [m.to_wire() for m in missions]
        sent = {m.mission_id for m in missions}
        label = name or queue_id

        async def run() -> bool:
            try:
                stored = await self.client.create_queue(settings.scheduler, wire, queue_id=queue_id, name=name or None,
                                                        params=dict(settings.params), workspace=self.workspace)
            except ApiError as exc:
                self._post_message(f"Queue not created: {exc.message}", False)
                return False
            except TransportError as exc:
                # No answer: the bridge may have stored it anyway. Creating again would make a duplicate.
                try:
                    stored = await self.client.queue(queue_id)
                except ApiError as lookup:
                    if lookup.status == 404:
                        self._post_message(f"Queue not created: {exc}", False)
                    else:
                        self._post_message(f"No answer while creating the queue; outcome unknown ({exc})", False)
                    return False
                except TransportError:
                    self._post_message(f"No answer while creating the queue; outcome unknown ({exc}). Check the queue list before creating it again.", False)
                    return False

            def created() -> None:
                self.draft.forget_created(sent)
                self._emit_message(f"Queue {label} created with {len(wire)} mission(s). Submit it to stage the first step on the robots.", True)
                queue = QueueExecution.from_json(stored)
                for callback in self._created_callbacks:
                    callback(queue)

            self._post(created)
            return True

        return self._run(CREATING, run())

    def upload(self) -> Future | None:
        """Replace the stored queue being edited with the draft (same id). It becomes CREATED, its last run is dropped."""
        queue_id = self.draft.queue_id
        if queue_id is None:
            return None
        if not self.draft.missions:
            self._emit_message("A queue needs at least one mission.", False)
            return None
        if self.draft.read_only or not self._sendable("Queue not changed"):
            return None
        settings = self.draft.settings
        wire = [m.to_wire() for m in self.draft.missions]
        label = self._label(queue_id)

        async def run() -> None:
            try:
                stored = await self.client.replace_queue(queue_id, settings.scheduler, wire, name=settings.name.strip() or None,
                                                         params=dict(settings.params), world_id=settings.world_id or None)
            except ApiError as exc:
                self._post_message(f"Queue {label} not changed: {exc.message}", False)
                return
            except TransportError as exc:
                self._post_message(f"No answer while changing queue {label} ({exc}); its content shows the outcome.", False)
                return

            def replaced() -> None:
                self.draft.sync(QueueExecution.from_json(stored))
                self._emit_message(f"Queue {label} changed. Submit it to run it.", True)

            self._post(replaced)

        return self._run(queue_id, run())

    def submit(self, queue_id: str) -> Future | None:
        label = self._label(queue_id)

        async def run() -> None:
            try:
                await self.client.submit_queue(queue_id)
            except ApiError as exc:
                self._post_message(f"Queue {label} not submitted: {exc.message}", False)
                return
            except TransportError as exc:
                self._post_message(f"No answer while submitting queue {label} ({exc}); its state shows the outcome.", False)
                return
            self._post_message(f"Queue {label} submitted: staging the first step on the robots.", True)

        return self._run(queue_id, run())

    def control(self, queue_id: str, command: str) -> Future | None:
        label = self._label(queue_id)

        async def run() -> None:
            try:
                await self.client.control_queue(queue_id, command)
            except ApiError as exc:
                self._post_message(f"Queue {label}: {command} failed: {exc.message}", False)
                return
            except TransportError as exc:
                self._post_message(f"Queue {label}: no answer to {command} ({exc}); its state shows the outcome.", False)
                return
            self._post_message(f"Queue {label}: {command} requested", True)

        return self._run(queue_id, run())

    def delete(self, queue_id: str) -> Future | None:
        label = self._label(queue_id)

        async def run() -> None:
            try:
                await self.client.delete_queue(queue_id)
            except ApiError as exc:
                self._post_message(f"Queue {label} not removed: {exc.message}", False)
                return
            except TransportError as exc:
                self._post_message(f"No answer while removing queue {label} ({exc})", False)
                return
            self._post_message(f"Queue {label} removed", True)

        return self._run(queue_id, run())

    # | ----------------------- internals ----------------------- |

    def _sendable(self, refused: str) -> bool:
        """Every mission has paths or areas that encode; otherwise the operator is told which one does not."""
        problems = self.draft.problems
        if problems:
            mission, problem = problems[0]
            self._emit_message(f"{refused}: mission '{mission.name or mission.mission_id}': {problem}", False)
        elif self.draft.robot_conflict:
            self._emit_message(f"{refused}: {self.draft.robot_conflict}", False)
        return not problems and not self.draft.robot_conflict

    def _label(self, queue_id: str) -> str:
        queue = self.executions.queue(queue_id)
        return queue.display_name if queue else queue_id

    def _post(self, update: Callable[[], None]) -> None:
        self._updates.put(update)

    def _emit_message(self, text: str, success: bool) -> None:
        print(("" if success else "error: ") + text)
        for callback in self._message_callbacks:
            callback(text, success)

    def _post_message(self, text: str, success: bool) -> None:
        self._post(lambda: self._emit_message(text, success))

    def _run(self, key: str, coro) -> Future | None:
        """One request per queue at a time; the key is pending (buttons disabled) until it is done."""
        if key in self.pending:
            coro.close()
            return None
        self.pending.add(key)
        self.executions.mark_changed()

        async def run():
            try:
                return await coro
            finally:
                def done() -> None:
                    self.pending.discard(key)
                    self.executions.mark_changed()
                self._post(done)

        return asyncio.run_coroutine_threadsafe(run(), self.api_loop)

    # | ----------------------- synchronization ----------------------- |

    async def _refresh(self) -> None:
        try:
            snapshot = await self.client.queues()
        except (ApiError, TransportError) as exc:
            def failed() -> None:
                self._refresh_pending = False
                self.executions.set_sync(SyncState.STALE, f"Snapshot failed: {exc}")
            self._post(failed)
            return

        received = time.time()

        def apply() -> None:
            self._refresh_pending = False
            self.executions.apply_snapshot(snapshot, received)
            if not self.schedulers:
                self.refresh_schedulers()  # the first attempt may have run before the fleet manager was up

        self._post(apply)

    def _apply_event(self, event: Mapping[str, Any]) -> None:
        if self.executions.apply_event(event, time.time()):
            self.refresh()

    async def _listen(self) -> None:
        url = f"{self.client.ws_base_url}/queues/events"
        backoff = RECONNECT_MIN
        while True:
            self._post(lambda: self.executions.set_sync(SyncState.CONNECTING))
            try:
                async with websockets.connect(url, open_timeout=10) as ws:
                    # Connected first, so no event is lost between the snapshot and the stream.
                    # Events received meanwhile are buffered by the connection and ordered by seq.
                    self._post(lambda: setattr(self, "_refresh_pending", True))
                    await self._refresh()
                    self._post(lambda: self.executions.set_sync(SyncState.LIVE))
                    backoff = RECONNECT_MIN
                    while True:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=RECONCILE_PERIOD)
                        except asyncio.TimeoutError:
                            self._post(self.refresh)
                            continue
                        event = json.loads(raw)
                        if isinstance(event, dict) and event.get("type") in ("queue", "queue_removed"):
                            self._post(lambda e=event: self._apply_event(e))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # connection refused, closed, malformed message...
                message = f"Queue events disconnected: {type(exc).__name__}: {exc}"
                self._post(lambda m=message: self.executions.set_sync(SyncState.STALE, m))
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX)
