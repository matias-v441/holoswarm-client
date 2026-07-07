import dearpygui.dearpygui as dpg
from data.mission import *
from data.session import *
from dataclasses import replace
from gui.explorer.items.waypoints import WaypointsNode

class ExplorerWindow:

    def __init__(
            self,
            mission: Mission,
            session: Session,
            tag: str = "explorer"
    ) -> None:
        self.tag = tag
        self.window_tag = f"{tag}_window"
        self.mission = mission
        self.session = session
        self.tracked_items: dict[str,WaypointsNode] = {}
            

    def add(self) -> None:

        with dpg.window(
            label="Path",
            tag=self.window_tag
        ):
            self._draw_items_tree()

        self.session.subscribe(lambda _: self._draw_items_tree())
        self.mission.subscribe(lambda _: self._draw_items_tree())

    
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


    