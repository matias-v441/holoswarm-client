import dearpygui.dearpygui as dpg
from holoswarm_client.gui.map.handlers.map_grid import MapGridHandlers
from holoswarm_client.gui.map.handlers.waypoints import WaypointsHandlers
from holoswarm_client.gui.map.handlers.coverage import CoverageHandlers
from holoswarm_client.gui.map.map import Map, ToolType

Point = tuple[float,float]

class Controller:

    def __init__(self, map: Map, drawlist_tag: str, *,
            map_grid: MapGridHandlers,
            waypoints: WaypointsHandlers,
            coverage: CoverageHandlers
        ):

        self.map_grid = map_grid
        self.waypoints = waypoints
        self.coverage = coverage
        self.drawlist_tag = drawlist_tag
        self.map = map

        with dpg.handler_registry():
            dpg.add_mouse_down_handler(button=dpg.mvMouseButton_Left, callback=self._on_left_down)
            dpg.add_mouse_drag_handler(button=dpg.mvMouseButton_Left, callback=self._on_left_drag)
            dpg.add_mouse_release_handler(button=dpg.mvMouseButton_Left, callback=self._on_left_release)
            dpg.add_mouse_wheel_handler(callback=self._on_wheel)

        self.mouse_left_down = False

    def _local_mouse_pos(self, mouse_pos: Point) -> Point:
        item_min = dpg.get_item_rect_min(self.drawlist_tag)
        return mouse_pos[0] - item_min[0], mouse_pos[1] - item_min[1]

    def _on_left_down(self):
        if not dpg.is_item_hovered(self.drawlist_tag):
            return 
        if self.mouse_left_down:
            return
        self.mouse_left_down = True 
        mouse_pos = self._local_mouse_pos(dpg.get_mouse_pos(local=False))
        if self.map.active_tool == ToolType.WAYPOINT:
            consumed = self.waypoints.on_down(mouse_pos)
        elif self.map.active_tool == ToolType.COVERAGE:
            consumed = self.coverage.on_down(mouse_pos)
        else:
            consumed = False
        if not consumed:
            self.map_grid.on_down()

    def _on_left_drag(self):
        self.map_grid.on_drag()

    def _on_left_release(self):
        self.mouse_left_down = False
        self.map_grid.on_release()

    def _on_wheel(self, sender=None, app_data=None, user_data=None):
        mouse_pos = self._local_mouse_pos(dpg.get_mouse_pos(local=False))
        self.map_grid.on_wheel(mouse_pos, app_data)
