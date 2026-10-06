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
        # Loaded areas are complete; newly drawn areas start with a single point.
        self._closed = len(self.mission.tasks[task_uuid].points) >= 3
        self._was_selected = bool(self.session.item_selected(task_uuid))
        self._redraw = lambda _: self.draw()
        self.mission.subscribe(self._redraw)
        self.session.subscribe(self._redraw)
        self.map = map
        self.items = set()

    @property
    def active(self):
        return self.task_uuid in self.mission.tasks

    def dispose(self) -> None:
        """The task is gone: stop drawing it."""
        self.mission.unsubscribe(self._redraw)
        self.session.unsubscribe(self._redraw)
        self.delete()

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
        selected = bool(self.session.item_selected(coverage.uuid))
        if self._was_selected and not selected and len(coverage.points) >= 3:
            self._closed = True
        self._was_selected = selected

        self.delete()
        self.items = set()

        if len(coverage.points) < 3 or not dpg.does_item_exist(self.drawlist_tag):
            return

        canvas_points = [
            self.map.world_to_canvas(self.map.latlon_to_world(latitude, longitude))
            for latitude, longitude in coverage.points
        ]
        # Tasks of the selected mission (or one being drawn) in orange, the rest of the queue dark orange.
        in_mission = coverage.mission_id is None or coverage.mission_id == self.session.selected_mission

        self.items.add(dpg.draw_polygon(
            canvas_points,
            color=(0, 0, 0, 0),
            fill=((255, 225, 126, 80) if self._hovered else (255, 205, 89, 40)) if in_mission else (180, 95, 30, 30),
            parent=self.drawlist_tag,
        ))
        self.items.add(dpg.draw_polyline(
            canvas_points,
            closed=self._closed,
            color=(255, 255, 255, 255) if selected else (255, 205, 89, 235) if in_mission else (180, 95, 30, 170),
            thickness=3 if selected else 2 if in_mission else 1.5,
            parent=self.drawlist_tag,
        ))

        
