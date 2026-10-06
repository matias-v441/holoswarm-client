"""Client mirror of the mission queues kept by the bridge.

The bridge owns the queues. This store holds the last confirmed view: a snapshot from GET /queues
plus the changes from /queues/events, ordered by the per-session sequence number. Every event
carries a whole queue (or its removal), so applying one replaces that queue.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class QueueState(Enum):
    CREATED = "CREATED"          # stored on the bridge only
    SUBMITTING = "SUBMITTING"    # being handed to the fleet manager
    UPLOADING = "UPLOADING"      # the fleet manager stages the first step on the robots
    READY = "READY"              # staged; waiting for start
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    FINISHED = "FINISHED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"        # staging failed; nothing ran, can be submitted again
    INTERRUPTED = "INTERRUPTED"  # the fleet manager (or the bridge) restarted while it ran
    UNKNOWN = "UNKNOWN"

    @property
    def terminal(self) -> bool:
        return self in (QueueState.FINISHED, QueueState.CANCELLED, QueueState.INTERRUPTED)

    @property
    def in_flight(self) -> bool:
        """Handed to the fleet manager and not done: it holds the robots."""
        return self in (QueueState.SUBMITTING, QueueState.UPLOADING, QueueState.READY, QueueState.RUNNING, QueueState.CANCELLING)

    @property
    def can_submit(self) -> bool:
        """Not handed to the fleet manager: a done queue can be submitted again and runs from scratch."""
        return not self.in_flight and self != QueueState.UNKNOWN

    @property
    def resubmit_replaces_run(self) -> bool:
        """Submitting it again replaces the progress of a run that happened."""
        return self in (QueueState.FINISHED, QueueState.CANCELLED, QueueState.INTERRUPTED)

    @property
    def can_start(self) -> bool:
        return self == QueueState.READY

    @property
    def can_cancel(self) -> bool:
        # INTERRUPTED by a bridge restart: the fleet manager may still run it
        return self in (QueueState.UPLOADING, QueueState.READY, QueueState.RUNNING, QueueState.INTERRUPTED)

    @property
    def can_pause(self) -> bool:
        return self == QueueState.RUNNING

    @property
    def can_delete(self) -> bool:
        return not self.in_flight and self != QueueState.UNKNOWN


class MissionState(Enum):
    QUEUED = "QUEUED"
    STAGED = "STAGED"            # uploaded to its robots, waiting for the queue to start
    DISPATCHED = "DISPATCHED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"

    @property
    def terminal(self) -> bool:
        return self in (MissionState.SUCCEEDED, MissionState.FAILED, MissionState.CANCELLED)


class SyncState(Enum):
    CONNECTING = "connecting"
    LIVE = "live"
    STALE = "stale"  # disconnected: showing the last known view


def _enum(cls, value: Any):
    try:
        return cls(value)
    except ValueError:
        return cls.UNKNOWN


@dataclass(frozen=True)
class MissionExecution:
    mission_id: str
    name: str = ""
    planner_type: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)
    priority: int = 0
    return_home_policy: int = 0
    state: MissionState = MissionState.UNKNOWN
    robots: tuple[str, ...] = ()
    progress: float = 0.0
    message: str = ""
    updated_at: float = 0.0

    @property
    def display_name(self) -> str:
        return self.name or self.mission_id

    @staticmethod
    def from_json(value: Mapping[str, Any]) -> "MissionExecution":
        details = value.get("details")
        return MissionExecution(
            mission_id=str(value["id"]),
            name=str(value.get("name", "")),
            planner_type=str(value.get("type", "")),
            details=details if isinstance(details, Mapping) else {},
            priority=int(value.get("priority", 0)),
            return_home_policy=int(value.get("return_home_policy", 0)),
            state=_enum(MissionState, value.get("state")),
            robots=tuple(value.get("robots", ())),
            progress=float(value.get("progress", 0.0)),
            message=str(value.get("message", "")),
            updated_at=float(value.get("updated_at", 0.0)),
        )


@dataclass(frozen=True)
class QueueExecution:
    queue_id: str
    name: str = ""
    scheduler: str = ""
    params: Mapping[str, Any] = field(default_factory=dict)
    world_id: str = ""
    state: QueueState = QueueState.UNKNOWN
    message: str = ""
    paused: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0
    missions: Mapping[str, MissionExecution] = field(default_factory=dict)  # execution order

    @property
    def display_name(self) -> str:
        return self.name or self.queue_id

    @property
    def done(self) -> int:
        return sum(1 for m in self.missions.values() if m.state.terminal)

    @staticmethod
    def from_json(value: Mapping[str, Any]) -> "QueueExecution":
        missions = [MissionExecution.from_json(m) for m in value.get("missions", ())]
        params = value.get("params")
        return QueueExecution(
            queue_id=str(value["queue_id"]),
            name=str(value.get("name", "")),
            scheduler=str(value.get("scheduler", "")),
            params=params if isinstance(params, Mapping) else {},
            world_id=str(value.get("world_id", "")),
            state=_enum(QueueState, value.get("state")),
            message=str(value.get("message", "")),
            paused=bool(value.get("paused", False)),
            created_at=float(value.get("created_at", 0.0)),
            updated_at=float(value.get("updated_at", 0.0)),
            missions={m.mission_id: m for m in missions},
        )


type Callback = Callable[["ExecutionStore"], None]


class ExecutionStore:
    """Last confirmed queue state. Mutated and read on the GUI thread only."""

    def __init__(self) -> None:
        self._queues: dict[str, QueueExecution] = {}
        self.session_id: str | None = None
        self.last_seq: int = 0
        self.sync: SyncState = SyncState.CONNECTING
        self.sync_message: str = ""
        self.updated_at: float = 0.0
        self._callbacks: list[Callback] = []
        self._dirty = True

    @property
    def queues(self) -> Mapping[str, QueueExecution]:
        """Creation order."""
        return MappingProxyType(self._queues)

    def queue(self, queue_id: str) -> QueueExecution | None:
        return self._queues.get(queue_id)

    def in_flight(self) -> QueueExecution | None:
        """The queue handed to the fleet manager, if any (at most one holds the robots)."""
        return next((q for q in self._queues.values() if q.state.in_flight), None)

    def apply_snapshot(self, snapshot: Mapping[str, Any], now: float) -> None:
        """Replace the view with a GET /queues response."""
        self.session_id = str(snapshot["session_id"])
        self.last_seq = int(snapshot.get("seq", 0))
        queues = [QueueExecution.from_json(value) for value in snapshot.get("queues", ())]
        self._queues = {q.queue_id: q for q in sorted(queues, key=lambda q: q.created_at)}
        self.updated_at = now
        self._dirty = True

    def apply_event(self, event: Mapping[str, Any], now: float) -> bool:
        """Apply one queue event. Returns True when the view can no longer be trusted and a snapshot is needed."""
        if self.session_id is None or event.get("session_id") != self.session_id:
            return True  # bridge restarted (or no snapshot yet)

        seq = int(event.get("seq", 0))
        if seq <= self.last_seq:
            return False  # already contained in the snapshot or a duplicate
        if seq != self.last_seq + 1:
            return True  # missed events

        kind = event.get("type")
        if kind == "queue":
            queue = QueueExecution.from_json(event["queue"])
            self._queues[queue.queue_id] = queue
        elif kind == "queue_removed":
            self._queues.pop(str(event.get("queue_id")), None)
        else:
            return True

        self.last_seq = seq
        self.updated_at = now
        self._dirty = True
        return False

    def set_sync(self, sync: SyncState, message: str = "") -> None:
        if (sync, message) != (self.sync, self.sync_message):
            self.sync = sync
            self.sync_message = message
            self._dirty = True

    def mark_changed(self) -> None:
        """Something shown with the queues changed outside the store (e.g. a request in flight)."""
        self._dirty = True

    def subscribe(self, callback: Callback) -> None:
        self._callbacks.append(callback)

    def notify(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        for callback in self._callbacks:
            callback(self)
