import dearpygui.dearpygui as dpg
from holoswarm_client.gui.map.map import Map
from holoswarm_client.data.mission import *
from holoswarm_client.data.session import *
from dataclasses import replace

from collections.abc import Callable

Point = tuple[float,float]

def ctrl_pressed() -> bool:
    return dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)

class CoverageHandlers:

    def __init__(self, map: Map, drawlist_tag: str):
        self.map = map
        self.drawlist_tag = drawlist_tag
        self.active_coverage_uuid:str = None
        self.mission = map.mission
        self.session = map.session
        self.session.subscribe(self.session_cb)

    def session_cb(self, session):
        self.active_coverage_uuid = None
        selection: Selection = session.selection
        if not selection:
            return

        tasks = self.mission.tasks
        for item_uuid in selection.items:
            if isinstance(tasks.get(item_uuid), Coverage):
                self.active_coverage_uuid = item_uuid
                return

    def on_down(self, mouse) -> bool:

        clicked_uuid = self._find_clicked_uuid(mouse)

        if not ctrl_pressed():
            if not clicked_uuid:
                selection = self.session.selection
                if selection:
                    self.session.pop(selection.uuid)
            return False

        if clicked_uuid:
            selection = self.session.selection
            if not selection:
                selection = Selection()
            selection = replace(selection, items=(clicked_uuid,))
            self.session.push(selection)
            return True

        origin = self.map.origin_canvas(self.map.width, self.map.height)
        world_at_mouse = self.map.canvas_to_world(mouse, origin)

        lat, lon = self.map.world_to_latlon(world_at_mouse)
 
        if not self.active_coverage_uuid:
            coverage = Coverage(
                points=((lat, lon),),
                time_interval=(0., 1.),
                height_id=0,
                height=5.,
            )
            self.mission.push_task(coverage)

            selection = self.session.selection
            if not selection:
                selection = Selection()
            selection = replace(selection, items=(coverage.uuid,))
            self.session.push(selection)
        else:
            coverage: Coverage = self.mission.tasks[self.active_coverage_uuid]
            coverage = replace(coverage, points=(*coverage.points, (lat, lon)))
            self.mission.push_task(coverage)
        return True

    def _find_clicked_uuid(self, mouse, radius=16.) -> str|None:
        radius_squared = radius * radius
        for coverage in self.mission.areas.values():
            if len(coverage.points) < 3:
                continue

            points = [
                self.map.world_to_canvas(self.map.latlon_to_world(lat, lon))
                for lat, lon in coverage.points
            ]
            min_x = min(point[0] for point in points)
            max_x = max(point[0] for point in points)
            min_y = min(point[1] for point in points)
            max_y = max(point[1] for point in points)
            if not (
                min_x - radius <= mouse[0] <= max_x + radius
                and min_y - radius <= mouse[1] <= max_y + radius
            ):
                continue

            inside = False
            previous = points[-1]
            for current in points:
                edge_x = current[0] - previous[0]
                edge_y = current[1] - previous[1]
                edge_length_squared = edge_x * edge_x + edge_y * edge_y
                if edge_length_squared:
                    projection = max(0., min(1., (
                        (mouse[0] - previous[0]) * edge_x
                        + (mouse[1] - previous[1]) * edge_y
                    ) / edge_length_squared))
                    dx = mouse[0] - (previous[0] + projection * edge_x)
                    dy = mouse[1] - (previous[1] + projection * edge_y)
                    if dx * dx + dy * dy <= radius_squared:
                        return coverage.uuid

                if ((current[1] > mouse[1]) != (previous[1] > mouse[1])
                        and mouse[0] < (previous[0] - current[0])
                        * (mouse[1] - current[1])
                        / (previous[1] - current[1]) + current[0]):
                    inside = not inside
                previous = current

            if inside:
                return coverage.uuid
        return None
                
