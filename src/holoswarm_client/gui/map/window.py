from collections.abc import Callable, Sequence
from PIL import Image
from io import BytesIO
import numpy as np
from math import floor
import dearpygui.dearpygui as dpg

from holoswarm_client.data.mission import Coverage, Mission, MissionTask, PointGlobal, PointLocal, Waypoints
from holoswarm_client.gui.map.map import Map, ToolType
from holoswarm_client.gui.map.controller import Controller
from holoswarm_client.gui.map.handlers.map_grid import MapGridHandlers
from holoswarm_client.gui.map.handlers.waypoints import WaypointsHandlers
from holoswarm_client.gui.map.handlers.coverage import CoverageHandlers
from holoswarm_client.gui.map.handlers.cursor import CursorHandlers
from holoswarm_client.gui.map.point_info import PointInfoWindow
from holoswarm_client.gui.map.views.waypoint import WaypointPrimitive
from holoswarm_client.gui.map.views.coverage import CoveragePrimitive
from holoswarm_client.gui.map.views.fleet import Fleet
from holoswarm_client.gui.map.views.safety_area import SafetyArea
import asyncio
from asyncio import AbstractEventLoop
from queue import SimpleQueue, Empty

from holoswarm_client.data.workspace import Workspace
from holoswarm_client.iroc.client import ApiError, ApiUnavailable, IROCClient

Point = tuple[float, float]

class MapGridWindow:
    def __init__(
        self,
        map: Map,
        client: IROCClient,
        api_loop: AbstractEventLoop,
        workspace: str = "temesvar",
        tag: str = "map_grid"
    ) -> None:
        self.tag = tag
        self.window_tag = f"{tag}_window"
        self.drawlist_tag = f"{tag}_drawlist"
        self.texture_registry_tag = f"{tag}_textures"
        self.texture_tag = f"{tag}_map_texture"
        self.info_tag = f"{tag}_info"
        self.world_combo_tag = f"{tag}_world_combo"
        self.handler_tag = f"{tag}_handlers"

        # The background is the workspace's orthophoto, served by the bridge (not stored locally).
        self.workspace = workspace
        # The workspace's worlds (from the bridge); the map shows the selected one, in its origin.
        self.workspace_data: Workspace | None = None
        self.workspace_error: str | None = None
        self.active_worlds: list[str] = []  # worlds whose safety area is the fleet's
        self.world_chosen = False           # picked by the user; otherwise the map follows the active world
        self.max_image_px = 4096
        self.image_bounds: tuple[float, float, float, float] | None = None  # west, south, east, north [deg]
        self.image_width = 0
        self.image_height = 0

        self.minor_line_min_thickness = 0.18
        self.max_minor_line_thickness = 1.2
        self.max_major_line_thickness = 2.4
        self.texture_loaded = False

        self.client = client
        self.map = map
        self.point_info = PointInfoWindow(client, api_loop)
        self.cursor = CursorHandlers(self.map, self.drawlist_tag, self.point_info.show)
        self.controller = Controller(self.map, self.drawlist_tag,
            map_grid=MapGridHandlers(self.map, self.drawlist_tag, lambda: self._ui_events.put(self._draw)),
            waypoints=WaypointsHandlers(self.map, self.drawlist_tag),
            coverage=CoverageHandlers(self.map, self.drawlist_tag),
            cursor=self.cursor
            )
        map.mission.subscribe(self._mission_callback)

        self.fleet = Fleet(map, self.drawlist_tag)
        self.safety_area = SafetyArea(self.map, self.drawlist_tag, client, api_loop, on_change=self._border_changed)

        self._tracked_primitives: dict[str,WaypointPrimitive] = {}
        self.api_loop = api_loop
        self._map_image_bytes = None

        self._ui_events = SimpleQueue()


    def add(self) -> None:

        self.request_workspace()
        self.request_map()
        self.safety_area.aquire()

        with dpg.window(
            tag=self.window_tag,
            no_close=True,  # part of the layout
        ):
            top_bar_tag = f"{self.window_tag}_top_bar"
            with dpg.group(tag=top_bar_tag, horizontal=True):
                dpg.add_combo(
                    items=[],
                    default_value="loading worlds...",
                    tag=self.world_combo_tag,
                    width=220,
                    callback=lambda _sender, value: self._world_picked(value),
                )
                # dpg.add_checkbox(
                #     label="local",
                #     default_value=self.fleet.use_local_poses,
                #     callback=lambda _sender, value: setattr(self.fleet, "use_local_poses", value),
                # )
                dpg.add_combo(
                    items=[tool.value for tool in ToolType],
                    default_value=self.map.active_tool.value,
                    width=100,
                    callback=lambda _sender, value: self._tool_picked(ToolType(value)),
                )
                dpg.add_text(self.info_text(), tag=self.info_tag)
            dpg.add_drawlist(width=-1, height=-1, tag=self.drawlist_tag)

        # Fit the window size
        def resize_callback(sender, app_data, user_data):
            self.map.width = dpg.get_item_width(self.window_tag)
            self.map.height = dpg.get_item_height(self.window_tag) - 60 # for the top bar
            dpg.configure_item(self.drawlist_tag,
                                width=self.map.width,
                                height=self.map.height)
            self._draw()
        drawlist_handler_tag = f"{self.window_tag}_drawlist_handler"
        with dpg.item_handler_registry(tag=drawlist_handler_tag):
            dpg.add_item_resize_handler(callback=resize_callback)
        dpg.bind_item_handler_registry(self.window_tag, drawlist_handler_tag)

        self._draw()


    def _mission_callback(self, mission: Mission) -> None:
        for view in self._tracked_primitives.values():
            if not view.active:
                view.dispose()
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
        for uuid in mission.areas.keys():
            if uuid not in self._tracked_primitives:
                view = CoveragePrimitive(self.map, self.drawlist_tag, uuid)
                self._tracked_primitives[uuid] = view
                view.draw()


    def request_workspace(self, attempt: int = 0):
        """Fetch the workspace's worlds from the bridge; retried while the bridge is not up yet."""
        future = asyncio.run_coroutine_threadsafe(self.client.workspace(self.workspace), self.api_loop)

        def on_workspace_acquired(future):
            try:
                workspace = Workspace.from_json(future.result())
            except Exception as e:
                print(f"Failed to acquire workspace {self.workspace}: {e}")
                if isinstance(e, ApiError) and not isinstance(e, ApiUnavailable) and e.status == 404:
                    self._ui_events.put(lambda: self._workspace_failed(f"workspace '{self.workspace}' not found"))
                    return
                delay = min(2.0 * (attempt + 1), 10.0)
                self.api_loop.call_soon_threadsafe(self.api_loop.call_later, delay, self.request_workspace, attempt + 1)
                return
            self._ui_events.put(lambda: self._workspace_loaded(workspace))

        future.add_done_callback(on_workspace_acquired)

    def _workspace_failed(self, message: str) -> None:
        self.workspace_error = message
        if dpg.does_item_exist(self.world_combo_tag):
            dpg.set_value(self.world_combo_tag, "no worlds")
        self._draw()

    def _workspace_loaded(self, workspace: Workspace) -> None:
        self.workspace_data = workspace
        self.workspace_error = None
        self._border_changed()
        if self.workspace_data.world(self.map.world_name) is None:
            self._follow_active_world(force=True)
        self._update_world_combo()

    def _border_changed(self) -> None:
        """The fleet's safety area changed: find the worlds it belongs to."""
        if self.workspace_data is None:
            return
        self.active_worlds = self.workspace_data.active_worlds(self.safety_area.points)
        self.safety_area.border_in_workspace = not self.safety_area.points or bool(self.active_worlds)
        self._follow_active_world()
        self._update_world_combo()
        self._draw()

    def _follow_active_world(self, force: bool = False) -> None:
        """Show an active world, unless the user picked one."""
        if self.workspace_data is None or not self.workspace_data.worlds:
            return
        if self.world_chosen or (not force and self.map.world_name in self.active_worlds):
            return
        if self.active_worlds:
            self.select_world(self.active_worlds[0])
        elif force:
            self.select_world(self.workspace_data.worlds[0].name)

    def _world_label(self, name: str) -> str:
        return f"{name} (active)" if name in self.active_worlds else name

    def _update_world_combo(self) -> None:
        if self.workspace_data is None or not dpg.does_item_exist(self.world_combo_tag):
            return
        dpg.configure_item(self.world_combo_tag, items=[self._world_label(w.name) for w in self.workspace_data.worlds])
        dpg.set_value(self.world_combo_tag, self._world_label(self.map.world_name) if self.map.world_name else "no worlds")

    def _world_picked(self, label: str) -> None:
        if self.workspace_data is None:
            return
        name = next((w.name for w in self.workspace_data.worlds if self._world_label(w.name) == label), None)
        if name is not None:
            self.world_chosen = True
            self.select_world(name)

    def select_world(self, name: str) -> None:
        """Show the world: its origin in the middle of the map, its safety area."""
        world = self.workspace_data.world(name) if self.workspace_data else None
        if world is None:
            return
        # The image stays where it is on the earth; the local frame moves to the world's origin.
        self.map.world_name = world.name
        self.map.origin_lat, self.map.origin_lon = world.origin
        self.map.pan_px = [0.0, 0.0]
        self._update_world_combo()
        self._draw()

    def request_map(self, attempt: int = 0):
        """Fetch the workspace's orthophoto from the bridge; retried while the bridge is not up yet."""
        future = asyncio.run_coroutine_threadsafe(
            self.client.workspace_map(self.workspace, self.max_image_px), self.api_loop)

        def on_map_acquired(future):
            try:
                image, bounds = future.result()
            except Exception as e:
                print(f"Failed to acquire the map of workspace {self.workspace}: {e}")
                delay = min(2.0 * (attempt + 1), 10.0)
                self.api_loop.call_soon_threadsafe(self.api_loop.call_later, delay, self.request_map, attempt + 1)
                return

            def apply():
                self._map_image_bytes = image
                self.image_bounds = bounds
                self.load_texture()
                self._draw()
            self._ui_events.put(apply)

        future.add_done_callback(on_map_acquired)

    def process_events(self):
        while True:
            try:
                event = self._ui_events.get_nowait()
            except Empty:
                break
            event.__call__()
        self.safety_area.process_events()
        self.fleet.process_events()
        self.point_info.process_events()
        self.controller.poll_mouse()

    def load_texture(self) -> None:
        image = Image.open(BytesIO(self._map_image_bytes)).convert("RGBA")
        width, height = image.size
        data = np.asarray(image, dtype=np.float32) / 255.0
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

    def _draw(self) -> None:
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

        #self.fleet.draw()
        self.safety_area.world = self.workspace_data.world(self.map.world_name) if self.workspace_data else None
        self.safety_area.world_active = self.map.world_name in self.active_worlds
        self.safety_area.draw()
        self.cursor.draw()

    def focus_tasks(self, tasks: Sequence[MissionTask]) -> None:
        """Centre the view on the paths and areas, zooming out when they do not fit."""
        points: list[Point] = []
        for task in tasks:
            if isinstance(task, Coverage):
                points += [self.map.latlon_to_world(lat, lon) for lat, lon in task.points]
            elif isinstance(task, Waypoints):
                for point in task.points:
                    if isinstance(point, PointGlobal):
                        points.append(self.map.latlon_to_world(point.lat, point.lon))
                    elif isinstance(point, PointLocal):
                        points.append(point.position[:2])
        if not points:
            return
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        if self.map.width > 0 and self.map.height > 0:
            fit = max((max(xs) - min(xs)) / (0.8 * self.map.width), (max(ys) - min(ys)) / (0.8 * self.map.height))
            if fit > self.map.meters_per_pixel:
                self.map.meters_per_pixel = min(fit, self.map.max_meters_per_pixel)
        centre = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
        self.map.pan_px = [-centre[0] / self.map.meters_per_pixel, centre[1] / self.map.meters_per_pixel]
        self._ui_events.put(self._draw)

    def _tool_picked(self, tool: ToolType) -> None:
        self.map.active_tool = tool
        self.cursor.draw()  # the cursor label shows with the cursor tool only

    def draw_map_image(self, width: int, height: int) -> None:
        if not self.texture_loaded or self.image_bounds is None:
            return

        origin = self.map.origin_canvas(width, height)
        west, south, east, north = self.image_bounds
        top_left = self.map.world_to_canvas(self.map.latlon_to_world(north, west), origin)
        bottom_right = self.map.world_to_canvas(self.map.latlon_to_world(south, east), origin)

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
            f"{self.workspace}: {self.fleet_text()} | "
            f"origin: {self.map.origin_lat:.4f}, {self.map.origin_lon:.4f} | "
            f"smallest grid cell: {self.map.grid_cell_meters():g} m | "
            f"scale: {self.map.meters_per_pixel:.3f} m/px"
        )

    def fleet_text(self) -> str:
        """Where the fleet flies, as far as its safety area tells."""
        if self.workspace_error:
            return self.workspace_error
        if self.workspace_data is None:
            return "loading"
        if not self.safety_area.points:
            return "fleet safety area unknown"
        if not self.active_worlds:
            return "fleet safety area is no world of the workspace"
        return "active: " + ", ".join(self.active_worlds)
