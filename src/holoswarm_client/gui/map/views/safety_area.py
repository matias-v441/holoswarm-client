import asyncio

import dearpygui.dearpygui as dpg

from holoswarm_client.gui.map.model import Map
from holoswarm_client.iroc.client import IROCClient
from asyncio import AbstractEventLoop

from queue import SimpleQueue, Empty

class SafetyArea:

    def __init__(self, map: Map, drawlist_tag: str, client: IROCClient, api_loop: AbstractEventLoop):
        self.map = map
        self.drawlist_tag = drawlist_tag
        self.points = []
        self.client = client
        self.api_loop = api_loop
        self.item: int | str | None = None
        self._ui_events = SimpleQueue()
    
    def aquire(self):
        future = asyncio.run_coroutine_threadsafe(
            self.client.get_borders(), self.api_loop
        )
        def on_completed(completed):
            print(completed)
            if completed.cancelled() or completed.exception() is not None:
                return
            result = completed.result()
            if not result.is_success:
                return
            self.points = [
                (point["x"], point["y"])
                for point in result.json()["points"]
            ]
            self.draw()
        future.add_done_callback(
            lambda res: self._ui_events.put(lambda: on_completed(res))
        )
    
    def process_events(self):
        while True:
            try:
                event = self._ui_events.get_nowait()
            except Empty:
                break
            event.__call__()

    def draw(self) -> None:
        if self.item is not None and dpg.does_item_exist(self.item):
            dpg.delete_item(self.item)
        self.item = None

        if len(self.points) < 3 or not dpg.does_item_exist(self.drawlist_tag):
            return

        canvas_points = [
            self.map.world_to_canvas(self.map.latlon_to_world(latitude, longitude))
            for latitude, longitude in self.points
        ]
        canvas_points.append(canvas_points[0])

        self.item = dpg.draw_polygon(
            canvas_points,
            color=(255, 205, 89, 255),
            fill=(255, 205, 89, 40),
            thickness=2,
            parent=self.drawlist_tag,
        )
