import dearpygui.dearpygui as dpg
from holoswarm_client.data.mission import *
from holoswarm_client.data.session import *
from holoswarm_client.gui.explorer.items.waypoints import WaypointsNode
from holoswarm_client.gui.explorer.items.coverage import CoverageNode
from holoswarm_client.data.queue_draft import QueueDraft
from holoswarm_client.iroc.mission_codec import CodecError, describe, encode_draft

from queue import SimpleQueue, Empty

class ExplorerWindow:

    def __init__(
            self,
            mission: Mission,
            session: Session,
            queue_draft: QueueDraft,
            tag: str = "explorer"
    ) -> None:
        self.tag = tag
        self.window_tag = f"{tag}_window"
        self.mission = mission
        self.session = session
        self.tracked_items: dict[str,WaypointsNode | CoverageNode] = {}
        self.queue_draft = queue_draft
        self._ui_events = SimpleQueue()
        self.response_text_tag = f"{self.window_tag}_response"

    def add(self) -> None:

        with dpg.window(
            label="Path",
            tag=self.window_tag
        ):
            with dpg.group(horizontal=True):
                dpg.add_button(label="Add to queue", tag=f"{self.window_tag}_add", callback=self._add_to_queue)
                with dpg.tooltip(f"{self.window_tag}_add"):
                    dpg.add_text("Freeze the current paths or area as the next mission of the new queue (Mission window).")
                dpg.add_button(label="Export JSON", callback=self._export)
            dpg.add_text("",tag=self.response_text_tag)
            self._draw_items_tree()

        self.session.subscribe(lambda _: self._draw_items_tree())
        self.mission.subscribe(lambda _: self._draw_items_tree())

    def process_events(self):
        while True:
            try:
                event = self._ui_events.get_nowait()
            except Empty:
                break
            event.__call__()
    
    def _draw_items_tree(self):
        for uuid,wp_node in self.tracked_items.items():
            wp_node: WaypointsNode
            wp_node.delete()
        self.tracked_items.clear()
        for wp in self.mission.waypoints.values():
            wp: Waypoints
            wp_node = WaypointsNode(self.mission, self.session, wp.uuid, f"{self.window_tag}_{wp.uuid}", self.window_tag)
            self.tracked_items[wp.uuid] = wp_node
            wp_node.draw()
        for wp in self.mission.areas.values():
            wp: Coverage
            wp_node = CoverageNode(self.mission, self.session, wp.uuid, f"{self.window_tag}_{wp.uuid}", self.window_tag)
            self.tracked_items[wp.uuid] = wp_node
            wp_node.draw()

    def _add_to_queue(self):
        # The whole collection is one mission; content that does not fit is refused, never dropped.
        try:
            payload = encode_draft(tuple(self.mission.tasks.values()), self.mission.robot_names, self.mission.robot_homes)
        except CodecError as e:
            dpg.set_value(self.response_text_tag, f"Cannot add: {e}")
            return
        added = self.queue_draft.add(payload, describe(payload))
        dpg.set_value(self.response_text_tag, f"Added '{added.name}' as mission {len(self.queue_draft)} of the new queue")

    def _export(self):
        try:
            path = self.mission.export_json()
        except (CodecError, OSError) as e:
            dpg.set_value(self.response_text_tag, f"Cannot export: {e}")
            return
        dpg.set_value(self.response_text_tag, f"Exported to {path}")
