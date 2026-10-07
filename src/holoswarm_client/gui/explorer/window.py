import dearpygui.dearpygui as dpg
from holoswarm_client.data.mission import *
from holoswarm_client.data.session import *
from holoswarm_client.gui.explorer.items.waypoints import WaypointsNode
from holoswarm_client.gui.explorer.items.coverage import CoverageNode
from holoswarm_client.data.queue_draft import QueueDraft

from queue import SimpleQueue, Empty

GREY = (150, 150, 150)
WARN = (230, 190, 90)


class ExplorerWindow:
    """The Task window: the selected task (one path or area) of the selected mission, to edit."""

    def __init__(
            self,
            mission: Mission,
            session: Session,
            queue_draft: QueueDraft,
            tag: str = "explorer",
    ) -> None:
        self.tag = tag
        self.window_tag = f"{tag}_window"
        self.mission = mission
        self.session = session
        self.tracked_items: dict[str,WaypointsNode | CoverageNode] = {}
        self.queue_draft = queue_draft
        self._ui_events = SimpleQueue()
        self.response_text_tag = f"{self.window_tag}_response"
        self.header_tag = f"{self.window_tag}_header"
        self.hint_tag = f"{self.window_tag}_hint"

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
            dpg.add_text("",tag=self.response_text_tag)
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
            dpg.set_value(self.response_text_tag, "Cannot export: select a mission with a path or area")
            return
        try:
            path = write_mission_file({"type": mission.planner_type, "uuid": mission.mission_id,
                                       "details": dict(mission.payload["details"])})
        except OSError as e:
            dpg.set_value(self.response_text_tag, f"Cannot export: {e}")
            return
        dpg.set_value(self.response_text_tag, f"Exported '{mission.name or mission.mission_id}' to {path}")
