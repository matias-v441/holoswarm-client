import dearpygui.dearpygui as dpg
from data.mission import Mission, Waypoints, PointLocal, PointGlobal
from data.session import Session
from gui.map.model import Map

from math import cos, sin

Point = tuple[float, float]

class WaypointPrimitive:
    
    def __init__(self, map: Map, drawlist_tag: str, task_uuid: str, label=""):
        self._label: str = label
        self._size: float = 16.0
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

    @property
    def size(self):
        return self._size

    def delete(self) -> None:
        for item in self.items:
            if dpg.does_item_exist(item):
                dpg.delete_item(item)

    def draw(self) -> None:

        if not self.active:
            self.delete()
            return

        if not isinstance(self.mission.tasks[self.task_uuid], Waypoints):
            raise ValueError(f"Task {self.task_uuid} should a waypoint")

        wp: Waypoints[PointLocal] = self.mission.tasks[self.task_uuid]

        if not isinstance(wp.points[0], PointLocal):
            raise NotImplementedError(f"Global frame is not supported")

        self.delete()
        self.items = set()

        wp_selected = self.session.item_selected(wp.uuid)

        canvas_points = [(self.map.world_to_canvas(p.position[:2]),p.heading) for p in wp.points]
        
        line_color = (255, 255, 255, 255) if wp_selected else (255, 205, 89, 235)
        thickness = 3 if wp_selected else 2

        for start, end in zip(canvas_points, canvas_points[1:]):
            self.items.update({
                dpg.draw_line(start[0], end[0], color=line_color, thickness=thickness, parent=self.drawlist_tag)
            })
        
        for center,heading in canvas_points:
            
            fill = (255, 225, 126, 255) if self._hovered else (255, 205, 89, 255)
            outline = (255, 255, 255, 255) if wp_selected else (42, 44, 50, 255)

            self.items.update({
                dpg.draw_polygon(
                    self._triangle_points(center, heading),
                    color=outline,
                    fill=fill,
                    thickness=2,
                    parent=self.drawlist_tag,
                ),
                dpg.draw_circle(
                    center,
                    self._size * 0.18,
                    color=outline,
                    fill=outline,
                    parent=self.drawlist_tag,
                )
            })

            if self._label:
                self.items.update({
                    dpg.draw_text(
                        (center[0] + self._size * 0.7, center[1] - self._size * 0.7),
                        self._label,
                        color=(230, 232, 238, 255),
                        parent=self.drawlist_tag,
                    )
                })

    def _triangle_points(self, center: Point, heading: float) -> list[Point]:
        direction = (cos(heading), -sin(heading))
        normal = (-direction[1], direction[0])

        tip = (
            center[0] + direction[0] * self._size,
            center[1] + direction[1] * self._size,
        )
        back = (
            center[0] - direction[0] * self._size * 0.55,
            center[1] - direction[1] * self._size * 0.55,
        )
        left = (
            back[0] + normal[0] * self._size * 0.55,
            back[1] + normal[1] * self._size * 0.55,
        )
        right = (
            back[0] - normal[0] * self._size * 0.55,
            back[1] - normal[1] * self._size * 0.55,
        )
        return [tip, left, right]
        