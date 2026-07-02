import dearpygui.dearpygui as dpg
from gui.map.model import Map

from collections.abc import Callable

Point = tuple[float,float]

class MapGridHandlers:

    def __init__(self, map: Map,  drawlist_tag: str, draw: Callable[[],None]):
        self.map = map
        self.drawlist_tag = drawlist_tag
        self.draw = draw
        self.drag_state = None

    def on_down(self, **kwargs) -> bool:
        
        if self.drag_state is None:
            self.drag_state = {
                "mouse": dpg.get_mouse_pos(local=False),
                "pan": tuple(self.map.pan_px),
            }
        return True

    def on_drag(self, **kwargs) -> bool:
        if self.drag_state is None:
            return False

        if not dpg.is_mouse_button_down(dpg.mvMouseButton_Left):
            return False

        mouse = dpg.get_mouse_pos(local=False)
        start_mouse = self.drag_state["mouse"]
        start_pan = self.drag_state["pan"]

        self.map.pan_px[0] = start_pan[0] + mouse[0] - start_mouse[0]
        self.map.pan_px[1] = start_pan[1] + mouse[1] - start_mouse[1]
        self.draw()
        return True

    def on_release(self, **kwargs) -> bool:
        self.drag_state = None
        return True

    def on_wheel(self, mouse, app_data) -> bool:
        if not dpg.is_item_hovered(self.drawlist_tag):
            return False

        zoom_factor = 1.15 if app_data > 0 else 1 / 1.15
        self.map.meters_per_pixel = min(
            self.map.max_meters_per_pixel,
            max(self.map.min_meters_per_pixel, self.map.meters_per_pixel / zoom_factor),
        )

        width = max(1, dpg.get_item_width(self.drawlist_tag))
        height = max(1, dpg.get_item_height(self.drawlist_tag))
        origin_before = self.map.origin_canvas(width, height)
        world_at_mouse = self.map.canvas_to_world(mouse, origin_before)

        origin_after = self.map.origin_canvas(width, height)
        mouse_after = self.map.world_to_canvas(world_at_mouse, origin_after)
        self.map.pan_px[0] += mouse[0] - mouse_after[0]
        self.map.pan_px[1] += mouse[1] - mouse_after[1]
        self.draw()
        return True

