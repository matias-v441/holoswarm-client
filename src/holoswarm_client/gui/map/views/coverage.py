import dearpygui.dearpygui as dpg
from holoswarm_client.data.mission import Mission, Coverage, PointLocal, PointGlobal
from holoswarm_client.data.session import Session
from holoswarm_client.gui.map.map import Map

from math import cos, sin

Point = tuple[float, float]

class CoveragePrimitive:
    
    def __init__(self, map: Map, drawlist_tag: str, task_uuid: str, label=""):
        self._label: str = label
        self._hovered: bool = False
        self.mission = map.mission
        self.session = map.session
        self.drawlist_tag = drawlist_tag
        self.task_uuid = task_uuid
        self.mission.subscribe(lambda _: self.draw())
        self.session.subscribe(lambda _: self.draw())
        self.map = map
        self.items = set()

    @property
    def active(self):
        return self.task_uuid in self.mission.tasks

    def delete(self) -> None:
        for item in self.items:
            if dpg.does_item_exist(item):
                dpg.delete_item(item)

    def draw(self) -> None:

        if not self.active:
            self.delete()
            return

        if not isinstance(self.mission.tasks[self.task_uuid], Coverage):
            raise ValueError(f"Task {self.task_uuid} should a coverage")

        coverage: Coverage = self.mission.tasks[self.task_uuid]

        self.delete()
        self.items = set()

        if len(coverage.points) < 3 or not dpg.does_item_exist(self.drawlist_tag):
            return

        canvas_points = [
            self.map.world_to_canvas(self.map.latlon_to_world(latitude, longitude))
            for latitude, longitude in coverage.points
        ]
        selected = self.session.item_selected(coverage.uuid)

        self.items.add(dpg.draw_polygon(
            canvas_points,
            color=(255, 255, 255, 255) if selected else (255, 205, 89, 235),
            fill=(255, 225, 126, 80) if self._hovered else (255, 205, 89, 40),
            thickness=3 if selected else 2,
            parent=self.drawlist_tag,
        ))

        
