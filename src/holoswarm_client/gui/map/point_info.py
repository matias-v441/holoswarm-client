import asyncio
from asyncio import AbstractEventLoop
from queue import SimpleQueue, Empty
from typing import Any

import dearpygui.dearpygui as dpg

from holoswarm_client.iroc.client import IROCClient


def gnss_origin_text(lat: float, lon: float, zone: str, x: float, y: float, amsl: float) -> str:
    """The point as the GNSS origin of the MRS simulator (hw_api.yaml), lat/lon and zone as comments."""
    return (
        f"#lat: {lat:.7f}\n"
        f"#lon: {lon:.7f}\n"
        f'#utm_zone: "{zone}"\n'
        f"utm_x: {x:.2f}\n"
        f"utm_y: {y:.2f}\n"
        f"amsl: {amsl:.1f}\n"
    )


class PointInfoWindow:
    """Coordinates and terrain height of a point picked on the map (cursor tool), from the bridge."""

    def __init__(self, client: IROCClient, api_loop: AbstractEventLoop, tag: str = "point_info"):
        self.client = client
        self.api_loop = api_loop
        self.window_tag = f"{tag}_window"
        self.latlon_tag = f"{tag}_latlon"
        self.utm_tag = f"{tag}_utm"
        self.amsl_tag = f"{tag}_amsl"
        self.copy_tag = f"{tag}_copy"
        self.copied_tag = f"{tag}_copied"

        self.point: tuple[float, float] | None = None
        self.text: str | None = None  # for the clipboard, once the bridge answered
        self._ui_events = SimpleQueue()

    def show(self, lat: float, lon: float) -> None:
        self.point = (lat, lon)
        self.text = None
        self._ensure_window()
        dpg.set_value(self.latlon_tag, f"lat, lon: {lat:.7f}, {lon:.7f}")
        dpg.set_value(self.utm_tag, "UTM:      loading...")
        dpg.set_value(self.amsl_tag, "AMSL:     loading...")
        dpg.set_value(self.copied_tag, "")
        dpg.configure_item(self.copy_tag, enabled=False)
        dpg.configure_item(self.window_tag, show=True)
        dpg.focus_item(self.window_tag)

        point = self.point
        future = asyncio.run_coroutine_threadsafe(self.client.terrain_height(lat, lon), self.api_loop)

        def done(future):
            try:
                result, error = future.result(), None
            except Exception as e:
                result, error = None, str(e) or type(e).__name__
            self._ui_events.put(lambda: self._answer(point, result, error))

        future.add_done_callback(done)

    def process_events(self) -> None:
        while True:
            try:
                event = self._ui_events.get_nowait()
            except Empty:
                break
            event()

    def _answer(self, point: tuple[float, float], result: dict[str, Any] | None, error: str | None) -> None:
        if point != self.point or not dpg.does_item_exist(self.window_tag):
            return  # an answer for an earlier point
        if error is not None:
            dpg.set_value(self.utm_tag, "UTM:      -")
            dpg.set_value(self.amsl_tag, f"AMSL:     failed: {error}")
            return

        utm = result["utm"]
        dpg.set_value(self.utm_tag, f"UTM:      {utm['zone']}  {utm['x']:.2f} E  {utm['y']:.2f} N")
        if not result.get("valid") or result.get("amsl") is None:
            dpg.set_value(self.amsl_tag, "AMSL:     no terrain height here")
            return
        dpg.set_value(self.amsl_tag, f"AMSL:     {result['amsl']:.1f} m")
        self.text = gnss_origin_text(*point, utm["zone"], utm["x"], utm["y"], result["amsl"])
        dpg.configure_item(self.copy_tag, enabled=True)

    def _copy(self) -> None:
        if self.text is None:
            return
        dpg.set_clipboard_text(self.text)
        dpg.set_value(self.copied_tag, "copied")

    def _ensure_window(self) -> None:
        if dpg.does_item_exist(self.window_tag):
            return
        # next to the picked point, inside the viewport
        width, height = 360, 170
        mouse_x, mouse_y = dpg.get_mouse_pos(local=False)
        x = max(0, min(int(mouse_x) + 20, dpg.get_viewport_client_width() - width - 10))
        y = max(0, min(int(mouse_y) + 20, dpg.get_viewport_client_height() - height - 10))
        with dpg.window(tag=self.window_tag, label="Point", width=width, height=height, no_collapse=True, pos=(x, y)):
            dpg.add_text("", tag=self.latlon_tag)
            dpg.add_text("", tag=self.utm_tag)
            dpg.add_text("", tag=self.amsl_tag)
            dpg.add_text("terrain dataset height (WGS84 ellipsoid), as the fleet manager's AMSL", color=(150, 150, 150),
                         wrap=340)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Copy", tag=self.copy_tag, enabled=False, callback=self._copy)
                dpg.add_text("", tag=self.copied_tag, color=(120, 200, 120))
