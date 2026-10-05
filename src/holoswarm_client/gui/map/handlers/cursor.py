import dearpygui.dearpygui as dpg
from holoswarm_client.gui.map.map import Map, ToolType

from collections.abc import Callable

Point = tuple[float,float]

CLICK_MAX_MOVE_PX = 4.0  # a press and release closer than this is a click, otherwise the map was dragged


class CursorHandlers:
    """The cursor tool: shows the lat/lon under the mouse; a click picks the point."""

    def __init__(self, map: Map, drawlist_tag: str, on_pick: Callable[[float, float], None]):
        self.map = map
        self.drawlist_tag = drawlist_tag
        self.on_pick = on_pick
        self.mouse: Point | None = None  # over the map, in drawlist coordinates
        self.press: Point | None = None
        self.items: list[int | str] = []

    def latlon_at(self, mouse: Point) -> tuple[float, float]:
        origin = self.map.origin_canvas(self.map.width, self.map.height)
        return self.map.world_to_latlon(self.map.canvas_to_world(mouse, origin))

    def on_move(self, mouse: Point) -> None:
        hovered = self.map.active_tool == ToolType.CURSOR and dpg.is_item_hovered(self.drawlist_tag)
        self.mouse = mouse if hovered else None
        self.draw()

    def on_down(self, mouse: Point) -> bool:
        self.press = mouse
        return False  # dragging still pans the map

    def on_release(self, mouse: Point) -> None:
        press, self.press = self.press, None
        if press is None or self.map.active_tool != ToolType.CURSOR:
            return
        if abs(mouse[0] - press[0]) <= CLICK_MAX_MOVE_PX and abs(mouse[1] - press[1]) <= CLICK_MAX_MOVE_PX:
            self.on_pick(*self.latlon_at(mouse))

    def draw(self) -> None:
        for item in self.items:
            if dpg.does_item_exist(item):
                dpg.delete_item(item)
        self.items = []

        if self.mouse is None or self.map.active_tool != ToolType.CURSOR or not dpg.does_item_exist(self.drawlist_tag):
            return

        x, y = self.mouse
        lat, lon = self.latlon_at(self.mouse)
        label = f"{lat:.7f}, {lon:.7f}"
        color = (255, 255, 255, 230)
        self.items.append(dpg.draw_line((x - 10, y), (x + 10, y), color=color, parent=self.drawlist_tag))
        self.items.append(dpg.draw_line((x, y - 10), (x, y + 10), color=color, parent=self.drawlist_tag))
        self.items.append(dpg.draw_rectangle((x + 12, y + 8), (x + 16 + 7.2 * len(label), y + 26),
                                             color=(0, 0, 0, 0), fill=(22, 24, 28, 200), parent=self.drawlist_tag))
        self.items.append(dpg.draw_text((x + 14, y + 10), label, color=color, size=14, parent=self.drawlist_tag))
