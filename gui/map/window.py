from collections.abc import Callable
from pathlib import Path
from PIL import Image
from io import BytesIO
import numpy as np
from math import floor
import dearpygui.dearpygui as dpg

from cuzk.client import CuzkOrtofotoClient
from data.mission import Mission
from gui.map.model import Map
from gui.map.controller import Controller
from gui.map.handlers.map_grid import MapGridHandlers
from gui.map.handlers.primitives import Primitives
from gui.map.views.waypoint import WaypointPrimitive

Point = tuple[float, float]

class MapGridWindow:
    def __init__(
        self,
        map: Map,
        tag: str = "map_grid"
    ) -> None:
        self.tag = tag
        self.window_tag = f"{tag}_window"
        self.drawlist_tag = f"{tag}_drawlist"
        self.texture_registry_tag = f"{tag}_textures"
        self.texture_tag = f"{tag}_map_texture"
        self.info_tag = f"{tag}_info"
        self.edit_button_tag = f"{tag}_edit_button"
        self.edit_window_tag = f"{tag}_edit_window"
        self.origin_lat_input_tag = f"{tag}_origin_lat_input"
        self.origin_lon_input_tag = f"{tag}_origin_lon_input"
        self.image_size_input_tag = f"{tag}_image_size_input"
        self.handler_tag = f"{tag}_handlers"

        self.image_size_m = 1000.
        self.image_width = 0
        self.image_height = 0

        self.minor_line_min_thickness = 0.18
        self.max_minor_line_thickness = 1.2
        self.max_major_line_thickness = 2.4
        self.texture_loaded = False

        self.cuzk_client = CuzkOrtofotoClient()
        self.map = map
        self.controller = Controller(self.map, self.drawlist_tag,
            map_grid=MapGridHandlers(self.map, self.drawlist_tag, self.draw),
            primitives=Primitives(self.map, self.drawlist_tag)
            )
        map.mission.subscribe(self._mission_callback)

        self._tracked_primitives: dict[str,WaypointPrimitive] = {}


    def add(self) -> None:

        self.load_texture()

        with dpg.window(
            tag=self.window_tag
        ):
            top_bar_tag = f"{self.window_tag}_top_bar"
            with dpg.group(tag=top_bar_tag, horizontal=True):
                dpg.add_text(self.info_text(), tag=self.info_tag)
                dpg.add_button(label="edit", tag=self.edit_button_tag, callback=self.open_edit_window)
            dpg.add_drawlist(width=-1, height=-1, tag=self.drawlist_tag)

        # Fit the window size
        def resize_callback(sender, app_data, user_data):
            self.map.width = dpg.get_item_width(self.window_tag)
            self.map.height = dpg.get_item_height(self.window_tag) - 60 # for the top bar
            dpg.configure_item(self.drawlist_tag,
                                width=self.map.width,
                                height=self.map.height)
            self.draw()
        drawlist_handler_tag = f"{self.window_tag}_drawlist_handler"
        with dpg.item_handler_registry(tag=drawlist_handler_tag):
            dpg.add_item_resize_handler(callback=resize_callback)
        dpg.bind_item_handler_registry(self.window_tag, drawlist_handler_tag)

        self.draw()


    def _mission_callback(self, mission: Mission) -> None:
        self._tracked_primitives = {
            uuid: view
            for uuid,view in self._tracked_primitives.items()
            if view.active
        }
        for uuid in mission.waypoints.keys():
            if uuid not in self._tracked_primitives:
                view = WaypointPrimitive(self.map, self.drawlist_tag, uuid)
                self._tracked_primitives[uuid] = view
                view.draw()


    def load_texture(self) -> None:
        size_m = (self.image_size_m, self.image_size_m)
        size_px = (int(size_m[0]/self.map.meters_per_pixel),int(size_m[1]/self.map.meters_per_pixel))
        print(f"Requesting image size {size_m} {size_px}")
        image_bytes = self.cuzk_client.get_square_image(self.map.origin_lon, self.map.origin_lat, size_m, size_px)
        Path("prague_ortofoto.png").write_bytes(image_bytes)
        image = Image.open(BytesIO(image_bytes)).convert("RGBA")
        width, height = image.size
        data = np.asarray(image, dtype=np.float32) / 255.0
        print(data.shape)
        assert width == size_px[0] and height == size_px[1], f"got {width}x{height} instead of {size_px}"
        data = data.ravel()

        texture_exists = dpg.does_item_exist(self.texture_tag)
        texture_size_changed = width != self.image_width or height != self.image_height

        if texture_exists and not texture_size_changed:
            dpg.set_value(self.texture_tag, data)
        else:
            if texture_exists:
                if dpg.does_item_exist(self.drawlist_tag):
                    dpg.delete_item(self.drawlist_tag, children_only=True)
                dpg.delete_item(self.texture_tag)

            if dpg.does_item_exist(self.texture_registry_tag):
                dpg.add_static_texture(width, height, data, tag=self.texture_tag, parent=self.texture_registry_tag)
            else:
                with dpg.texture_registry(tag=self.texture_registry_tag):
                    dpg.add_static_texture(width, height, data, tag=self.texture_tag)

        self.image_width = width
        self.image_height = height
        self.texture_loaded = True

    def open_edit_window(self) -> None:
        if dpg.does_item_exist(self.edit_window_tag):
            self.sync_edit_window()
            dpg.configure_item(self.edit_window_tag, show=True)
            dpg.focus_item(self.edit_window_tag)
            return

        with dpg.window(
            tag=self.edit_window_tag,
            label="Map Grid",
            width=280,
            height=150,
            pos=(20, 60),
            no_collapse=True,
        ):
            dpg.add_input_text(label="origin lat", tag=self.origin_lat_input_tag, width=120)
            dpg.add_input_text(label="origin lon", tag=self.origin_lon_input_tag, width=120)
            dpg.add_input_text(label="image size m", tag=self.image_size_input_tag, width=120)
            dpg.add_button(label="Load", callback=self.on_load_edit_window)

        self.sync_edit_window()

    def sync_edit_window(self) -> None:
        if not dpg.does_item_exist(self.edit_window_tag):
            return

        dpg.set_value(self.origin_lat_input_tag, f"{self.map.origin_lat:.7f}")
        dpg.set_value(self.origin_lon_input_tag, f"{self.map.origin_lon:.7f}")
        dpg.set_value(self.image_size_input_tag, f"{self.image_size_m:g}")

    def on_load_edit_window(self) -> None:
        try:
            origin_lat = float(dpg.get_value(self.origin_lat_input_tag))
            origin_lon = float(dpg.get_value(self.origin_lon_input_tag))
            image_size_m = float(dpg.get_value(self.image_size_input_tag))
        except ValueError:
            self.sync_edit_window()
            return

        self.map.origin_lat = origin_lat
        self.map.origin_lon = origin_lon
        self.image_size_m = image_size_m
        self.load_texture()
        self.draw()

    def draw(self) -> None:
        if not dpg.does_item_exist(self.drawlist_tag):
            return

        dpg.delete_item(self.drawlist_tag, children_only=True)

        width = max(1, dpg.get_item_width(self.drawlist_tag))
        height = max(1, dpg.get_item_height(self.drawlist_tag))

        dpg.draw_rectangle(
            (0, 0),
            (width, height),
            color=(62, 66, 72),
            fill=(22, 24, 28),
            parent=self.drawlist_tag,
        )

        self.draw_map_image(width, height)
        self.draw_grid(width, height)
        self.draw_origin(width, height)

        if dpg.does_item_exist(self.info_tag):
            dpg.set_value(self.info_tag, self.info_text())

        for primitive in self._tracked_primitives.values():
            primitive.draw()

    def draw_map_image(self, width: int, height: int) -> None:
        if not self.texture_loaded:
            return

        origin = self.map.origin_canvas(width, height)
        image_width_m = self.image_size_m
        image_height_m = self.image_size_m * (self.image_height / self.image_width)
        top_left = self.map.world_to_canvas((-image_width_m / 2, image_height_m / 2), origin)
        bottom_right = self.map.world_to_canvas((image_width_m / 2, -image_height_m / 2), origin)

        dpg.draw_image(
            self.texture_tag,
            top_left,
            bottom_right,
            parent=self.drawlist_tag,
        )

    def draw_grid(self, width: int, height: int) -> None:
        origin = self.map.origin_canvas(width, height)
        minor_cell_m = self.map.grid_cell_meters()
        minor_px = minor_cell_m / self.map.meters_per_pixel
        major_cell_m = minor_cell_m * 10
        major_px = major_cell_m / self.map.meters_per_pixel

        minor_thickness = min(self.max_minor_line_thickness, minor_px / 80)
        major_thickness = min(self.max_major_line_thickness, max(0.7, major_px / 100))

        if minor_thickness >= self.minor_line_min_thickness:
            self.draw_grid_lines(width, height, origin, minor_cell_m, (120, 128, 138, 80), minor_thickness)

        if major_px >= 6:
            self.draw_grid_lines(width, height, origin, major_cell_m, (210, 218, 230, 145), major_thickness)

    def draw_grid_lines(
        self,
        width: int,
        height: int,
        origin: Point,
        cell_m: float,
        color: tuple[int, int, int, int],
        thickness: float,
    ) -> None:
        min_world = self.map.canvas_to_world((0, height), origin)
        max_world = self.map.canvas_to_world((width, 0), origin)

        x = floor(min_world[0] / cell_m) * cell_m
        while x <= max_world[0]:
            canvas_x, _ = self.map.world_to_canvas((x, 0), origin)
            dpg.draw_line((canvas_x, 0), (canvas_x, height), color=color, thickness=thickness, parent=self.drawlist_tag)
            x += cell_m

        y = floor(min_world[1] / cell_m) * cell_m
        while y <= max_world[1]:
            _, canvas_y = self.map.world_to_canvas((0, y), origin)
            dpg.draw_line((0, canvas_y), (width, canvas_y), color=color, thickness=thickness, parent=self.drawlist_tag)
            y += cell_m

    def draw_origin(self, width: int, height: int) -> None:
        origin = self.map.origin_canvas(width, height)
        dpg.draw_circle(origin, 5, color=(255, 255, 255), fill=(255, 205, 89), parent=self.drawlist_tag)
        dpg.draw_line((origin[0] - 12, origin[1]), (origin[0] + 12, origin[1]), color=(255, 255, 255), parent=self.drawlist_tag)
        dpg.draw_line((origin[0], origin[1] - 12), (origin[0], origin[1] + 12), color=(255, 255, 255), parent=self.drawlist_tag)
        dpg.draw_text(
            (origin[0] + 9, origin[1] + 8),
            f"{self.map.origin_lat:.4f}, {self.map.origin_lon:.4f}",
            color=(255, 255, 255),
            parent=self.drawlist_tag,
        )

    def info_text(self) -> str:
        return (
            f"origin: {self.map.origin_lat:.4f}, {self.map.origin_lon:.4f} | "
            f"smallest grid cell: {self.map.grid_cell_meters():g} m | "
            f"scale: {self.map.meters_per_pixel:.3f} m/px"
        )
