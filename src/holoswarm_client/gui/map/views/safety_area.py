import asyncio
from collections.abc import Callable

import dearpygui.dearpygui as dpg

from holoswarm_client.data.workspace import LatLon, World, border_latlon
from holoswarm_client.gui.map.map import Map
from holoswarm_client.iroc.client import IROCClient
from asyncio import AbstractEventLoop

from queue import SimpleQueue, Empty

ACTIVE_COLOR = (255, 205, 89)     # the fleet's safety area
FOREIGN_COLOR = (255, 99, 71)     # the fleet's safety area, when it is no world of the workspace
WORLD_COLOR = (150, 200, 255)     # the selected world, when the fleet does not fly in it


class SafetyArea:
    """The fleet's safety area, polled from the bridge, and the safety area of the selected world."""

    poll_period_s = 5.0
    retry_period_s = 2.0

    def __init__(self, map: Map, drawlist_tag: str, client: IROCClient, api_loop: AbstractEventLoop,
                 on_change: Callable[[], None] | None = None):
        self.map = map
        self.drawlist_tag = drawlist_tag
        self.points: list[LatLon] | None = None  # the fleet's border; None until known
        self.client = client
        self.api_loop = api_loop
        self.on_change = on_change

        self.world: World | None = None  # the selected world
        self.world_active = False        # its safety area is the fleet's
        self.border_in_workspace = True  # the fleet's area is the area of some world of the workspace

        self.items: list[int | str] = []
        self._ui_events = SimpleQueue()

    def aquire(self):
        future = asyncio.run_coroutine_threadsafe(
            self.client.get_borders(), self.api_loop
        )
        def on_completed(completed):
            delay = self.retry_period_s
            try:
                result = completed.result()
                if result.is_success:
                    points = border_latlon(result.json())
                    delay = self.poll_period_s
                    if points != self.points:
                        self.points = points
                        if self.on_change is not None:
                            self.on_change()
                        self.draw()
            except Exception as e:
                print(f"Failed to acquire the safety area: {e}")
            self.api_loop.call_soon_threadsafe(self.api_loop.call_later, delay, self.aquire)
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
        for item in self.items:
            if dpg.does_item_exist(item):
                dpg.delete_item(item)
        self.items = []

        if not dpg.does_item_exist(self.drawlist_tag):
            return

        if self.world is not None and not self.world_active:
            self._draw_polygon(self.world.safety_area, WORLD_COLOR, fill_alpha=15)
        if self.points:
            self._draw_polygon(self.points, ACTIVE_COLOR if self.border_in_workspace else FOREIGN_COLOR, fill_alpha=40)

    def _draw_polygon(self, points, color: tuple[int, int, int], fill_alpha: int) -> None:
        if len(points) < 3:
            return
        canvas_points = [
            self.map.world_to_canvas(self.map.latlon_to_world(latitude, longitude))
            for latitude, longitude in points
        ]
        canvas_points.append(canvas_points[0])

        self.items.append(dpg.draw_polygon(
            canvas_points,
            color=(*color, 255),
            fill=(*color, fill_alpha),
            thickness=2,
            parent=self.drawlist_tag,
        ))
