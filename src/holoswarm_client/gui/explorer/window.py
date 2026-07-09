import dearpygui.dearpygui as dpg
from holoswarm_client.data.mission import *
from holoswarm_client.data.session import *
from holoswarm_client.gui.explorer.items.waypoints import WaypointsNode
from holoswarm_client.iroc.client import IROCClient

from dataclasses import replace
import asyncio
from asyncio import AbstractEventLoop
from queue import SimpleQueue, Empty

class ExplorerWindow:

    def __init__(
            self,
            mission: Mission,
            session: Session,
            client: IROCClient,
            api_loop: AbstractEventLoop,
            tag: str = "explorer"
    ) -> None:
        self.tag = tag
        self.window_tag = f"{tag}_window"
        self.mission = mission
        self.session = session
        self.tracked_items: dict[str,WaypointsNode] = {}
        self.client = client
        self.api_loop = api_loop
        self._ui_events = SimpleQueue()
        self.response_text_tag = f"{self.window_tag}_response"

    def add(self) -> None:

        with dpg.window(
            label="Path",
            tag=self.window_tag
        ):
            with dpg.group(horizontal=True):
                dpg.add_button(label="Upload mission", callback=self._upload_mission)
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

    def _upload_mission(self):
        mission_json = self.mission.to_json()
        future = asyncio.run_coroutine_threadsafe(self.client.upload_mission(mission_json), self.api_loop)
        def on_mission_uploaded(future):
            try:
                res = future.result()
                print("result:", res)
                def show_result():
                    dpg.set_value(self.response_text_tag, str(res))
                self._ui_events.put(show_result)
            except Exception as e:
                error = str(e)
                print("error: ", error)
                def show_result():
                    dpg.set_value(self.response_text_tag, error)
                self._ui_events.put(show_result)
        future.add_done_callback(on_mission_uploaded)
