import asyncio
import dearpygui.dearpygui as dpg
from typing import Any
from holoswarm_client.data.mission import *
from holoswarm_client.data.monitoring import Monitoring
from holoswarm_client.data.session import *
from holoswarm_client.gui.explorer.items.waypoints import WaypointsNode
from holoswarm_client.gui.explorer.items.coverage import CoverageNode
from holoswarm_client.data.queue_draft import QueueDraft
from holoswarm_client.iroc.client import ApiError, IROCClient

from queue import SimpleQueue, Empty

GREY = (150, 150, 150)
WARN = (230, 190, 90)
RED = (230, 90, 90)
TEXT = (200, 200, 200)


def _robot_failures(body: Any) -> str:
    """The robots of a /mission response that refused, one per line (those that only repeat the message are left out)."""
    if not isinstance(body, dict) or not isinstance(body.get("robot_results"), list):
        return ""
    lines = [f"\n  {r.get('robot')}: {r.get('message')}" for r in body["robot_results"]
             if isinstance(r, dict) and not r.get("success") and r.get("message") != body.get("message")]
    return "".join(lines)


class ExplorerWindow:
    """The Task window: the selected task (one path or area) of the selected mission, to edit.

    The selected mission can also be sent alone through the bridge's original /mission endpoint, outside of any
    queue, and started, paused or stopped there; its /mission/feedback state is shown.
    """

    def __init__(
            self,
            mission: Mission,
            session: Session,
            queue_draft: QueueDraft,
            client: IROCClient,
            api_loop: asyncio.AbstractEventLoop,
            monitoring: Monitoring,
            tag: str = "explorer",
    ) -> None:
        self.tag = tag
        self.window_tag = f"{tag}_window"
        self.mission = mission
        self.session = session
        self.client = client
        self.api_loop = api_loop
        self._single_pending = False  # a /mission request is in flight
        self.tracked_items: dict[str,WaypointsNode | CoverageNode] = {}
        self.queue_draft = queue_draft
        self._ui_events = SimpleQueue()
        self.response_text_tag = f"{self.window_tag}_response"
        self.header_tag = f"{self.window_tag}_header"
        self.hint_tag = f"{self.window_tag}_hint"
        self.single_state_tag = f"{self.window_tag}_single_state"
        monitoring.subscribe(self._draw_single_state)

    def add(self) -> None:

        with dpg.window(
            label="Task",
            tag=self.window_tag,
            no_close=True,  # part of the layout
        ):
            dpg.add_text("", tag=self.header_tag, wrap=0)
            dpg.add_text("", tag=self.hint_tag, color=GREY, wrap=0)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Export JSON", tag=f"{self.window_tag}_export", callback=self._export)
                with dpg.tooltip(f"{self.window_tag}_export"):
                    dpg.add_text("Write the selected mission to a mission file.")
            dpg.add_text("Single mission, outside the queues", color=GREY)
            with dpg.group(horizontal=True):
                self._button("send", "Send mission", self._send_single,
                             "Upload the selected mission to its robots through /mission, outside of any queue.\n"
                             "Nothing moves until Start. Refused while a queue is submitted; while it is sent,\n"
                             "queues cannot be submitted until it finishes or is stopped.")
                self._button("start", "Start", lambda: self._control_single("start"),
                             "Start the sent mission, or resume it when paused.")
                self._button("pause", "Pause", lambda: self._control_single("pause"), "Pause the running mission.")
                self._button("stop", "Stop", lambda: self._control_single("stop"),
                             "Abort the running mission, or unload the sent one from the robots.")
            dpg.add_text("", tag=self.single_state_tag, color=GREY, wrap=0)
            dpg.add_text("", tag=self.response_text_tag, wrap=0)
            self._draw_items_tree()

        self.session.subscribe(lambda _: self._draw_items_tree())
        self.mission.subscribe(lambda _: self._draw_items_tree())
        self.queue_draft.subscribe(lambda _: self._draw_header())

    def process_events(self):
        while True:
            try:
                event = self._ui_events.get_nowait()
            except Empty:
                break
            event.__call__()

    def _button(self, name: str, label: str, callback, tooltip: str) -> None:
        tag = f"{self.window_tag}_{name}"
        dpg.add_button(label=label, tag=tag, callback=callback)
        with dpg.tooltip(tag):
            dpg.add_text(tooltip)

    def _show_response(self, text: str, success: bool = True) -> None:
        dpg.set_value(self.response_text_tag, text)
        dpg.configure_item(self.response_text_tag, color=TEXT if success else RED)

    def _queue_label(self) -> str:
        draft = self.queue_draft
        if not draft.editing:
            return "the new queue"
        return f"queue {draft.settings.name or draft.queue_id}"

    def _draw_header(self) -> None:
        if not dpg.does_item_exist(self.header_tag):
            return
        draft = self.queue_draft
        mission = draft.mission(self.session.selected_mission)
        if mission is None:
            header = f"No mission selected in {self._queue_label()}"
        else:
            tasks = self.mission.tasks_of(mission.mission_id)
            task = self.session.selected_task
            position = next((i for i, t in enumerate(tasks) if t.uuid == task), None)
            header = f"Mission '{mission.name or mission.mission_id}' of {self._queue_label()}"
            if position is not None and len(tasks) > 1:
                header += f", task {position + 1} of {len(tasks)}"
        dpg.set_value(self.header_tag, header)

        if draft.read_only:
            hint, color = f"The {self._queue_label()} is submitted: shown read-only. Cancel it to change it.", WARN
        elif self.session.selected_task is None:
            hint, color = ("Ctrl+click a path or area on the map to select it; Ctrl+click empty map to draw a new one "
                           "in the selected mission, or in a new mission when it does not fit there (a second area, "
                           "a path for a robot that has one)."), GREY
        else:
            hint, color = "Changes are applied to the mission right away.", GREY
        error = draft.error(mission.mission_id) if mission is not None else None
        if error and not draft.read_only:
            hint, color = f"The mission cannot be sent like this: {error}", (230, 90, 90)
        dpg.set_value(self.hint_tag, hint)
        dpg.configure_item(self.hint_tag, color=color)
        self._update_single_controls()

    def _update_single_controls(self) -> None:
        mission = self.queue_draft.mission(self.session.selected_mission)
        sendable = mission is not None and not mission.empty and not self.queue_draft.error(mission.mission_id)
        dpg.configure_item(f"{self.window_tag}_send", enabled=sendable and not self._single_pending,
                           label="Sending..." if self._single_pending else "Send mission")
        for name in ("start", "pause", "stop"):
            dpg.configure_item(f"{self.window_tag}_{name}", enabled=not self._single_pending)

    def _draw_single_state(self, monitoring: Monitoring) -> None:
        state = monitoring.mission_state
        if state is None or not dpg.does_item_exist(self.single_state_tag):
            return
        text = f"Last feedback: {state.mission_state} ({state.progress:.0f} %)"
        if state.message:
            text += f": {state.message}"
        dpg.set_value(self.single_state_tag, text)

    def _draw_items_tree(self):
        for uuid,wp_node in self.tracked_items.items():
            wp_node: WaypointsNode
            wp_node.delete()
        self.tracked_items.clear()
        self._draw_header()
        task = self.mission.tasks.get(self.session.selected_task or "")
        if isinstance(task, Waypoints):
            node = WaypointsNode(self.mission, self.session, task.uuid, f"{self.window_tag}_{task.uuid}", self.window_tag)
        elif isinstance(task, Coverage):
            node = CoverageNode(self.mission, self.session, task.uuid, f"{self.window_tag}_{task.uuid}", self.window_tag)
        else:
            return
        self.tracked_items[task.uuid] = node
        node.draw()

    def _export(self):
        mission = self.queue_draft.mission(self.session.selected_mission)
        if mission is None or mission.empty:
            self._show_response("Cannot export: select a mission with a path or area", False)
            return
        try:
            path = write_mission_file({"type": mission.planner_type, "uuid": mission.mission_id,
                                       "details": dict(mission.payload["details"])})
        except OSError as e:
            self._show_response(f"Cannot export: {e}", False)
            return
        self._show_response(f"Exported '{mission.name or mission.mission_id}' to {path}")

    # | ----------------------- single mission (/mission) ----------------------- |

    def _send_single(self) -> None:
        mission = self.queue_draft.mission(self.session.selected_mission)
        if mission is None or mission.empty:
            self._show_response("Cannot send: select a mission with a path or area", False)
            return
        error = self.queue_draft.error(mission.mission_id)
        if error:
            self._show_response(f"Cannot send: {error}", False)
            return
        payload = {"type": mission.planner_type, "uuid": mission.mission_id, "details": dict(mission.payload["details"])}
        self._request_single(f"Send mission '{mission.name or mission.mission_id}'", self.client.send_mission(payload))

    def _control_single(self, command: str) -> None:
        self._request_single(f"{command.capitalize()} mission", self.client.control_mission(command))

    def _request_single(self, action: str, coro) -> None:
        """One /mission request at a time; its outcome (and the robots that refused) is shown when it is done."""
        if self._single_pending:
            coro.close()
            return
        self._single_pending = True
        self._update_single_controls()
        future = asyncio.run_coroutine_threadsafe(coro, self.api_loop)

        def done(future) -> None:
            try:
                body = future.result()
                text, success = f"{action}: {body.get('message') or 'done'}", True
            except ApiError as exc:
                text, success = f"{action} refused: {exc.message}{_robot_failures(exc.body)}", False
            except Exception as exc:  # no answer (timeout, unreachable bridge)
                text, success = f"{action}: no answer ({exc}); the robots' state shows the outcome", False
            print(("" if success else "error: ") + text)

            def show() -> None:
                self._single_pending = False
                self._show_response(text, success)
                self._update_single_controls()

            self._ui_events.put(show)

        future.add_done_callback(done)
