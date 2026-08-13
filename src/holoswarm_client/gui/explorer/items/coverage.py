import dearpygui.dearpygui as dpg
from holoswarm_client.data.mission import *
from holoswarm_client.data.session import *
from dataclasses import replace

class CoverageNode:
    def __init__(self, mission: Mission, session: Session, uuid: str, tag: str, parent_tag: str):
        self.mission = mission
        self.session = session
        self.uuid = uuid
        self.tag = tag
        self.parent_tag = parent_tag
    
    def delete(self):
        if dpg.does_item_exist(self.tag):
            dpg.delete_item(self.tag)

    def draw(self):
        if self.uuid not in self.mission.areas:
            self.delete()
        wp: Coverage = self.mission.areas[self.uuid]
        with dpg.tree_node(tag=self.tag, label=f"Area {self.uuid}", parent=self.parent_tag, default_open=True):
            dpg.add_button(label="Delete", callback=self._on_delete)
            assigned_robots = set(wp.assigned_robots)
            for robot_name in self.mission.robot_names:
                dpg.add_checkbox(
                    label=robot_name,
                    default_value=robot_name in assigned_robots,
                    callback=self._on_robot_assignment_changed,
                    user_data=robot_name,
                )
            dpg.add_input_text(
                label="height id",
                default_value=str(wp.height_id),
                width=120,
                callback=self._on_height_id_changed,
                on_enter=True,
            )
            dpg.add_input_float(
                label="height",
                default_value=wp.height,
                width=120,
                callback=self._on_height_changed,
                on_enter=True,
            )
            for index, (lat, lon) in enumerate(wp.points):
                with dpg.tree_node(label=f"Point {index}", default_open=True):
                    dpg.add_input_float(
                        label="lat",
                        default_value=lat,
                        width=120,
                        callback=self._on_point_changed,
                        user_data=(index, 0),
                        on_enter=True,
                    )
                    dpg.add_input_float(
                        label="lon",
                        default_value=lon,
                        width=120,
                        callback=self._on_point_changed,
                        user_data=(index, 1),
                        on_enter=True,
                    )

    def _on_height_id_changed(self, sender, app_data, user_data=None) -> None:
        coverage: Coverage = self.mission.areas[self.uuid]
        height_id = int(app_data) if app_data.isdecimal() else app_data
        self.mission.push_task(replace(coverage, height_id=height_id))

    def _on_height_changed(self, sender, app_data, user_data=None) -> None:
        coverage: Coverage = self.mission.areas[self.uuid]
        self.mission.push_task(replace(coverage, height=float(app_data)))

    def _on_point_changed(self, sender, app_data, user_data) -> None:
        point_index, coordinate_index = user_data
        coverage: Coverage = self.mission.areas[self.uuid]
        points = list(coverage.points)
        point = list(points[point_index])
        point[coordinate_index] = float(app_data)
        points[point_index] = tuple(point)
        self.mission.push_task(replace(coverage, points=tuple(points)))

    def _on_robot_assignment_changed(self, sender, app_data, robot_name: str) -> None:
        coverage: Coverage = self.mission.areas[self.uuid]
        assigned_robots = set(coverage.assigned_robots)
        if app_data:
            assigned_robots.add(robot_name)
        else:
            assigned_robots.discard(robot_name)
        self.mission.push_task(replace(
            coverage,
            assigned_robots=tuple(
                name for name in self.mission.robot_names if name in assigned_robots
            ),
        ))

    def _on_delete(self, sender=None, app_data=None, user_data=None) -> None:
        if self.session.item_selected(self.uuid):
            selection = self.session.selection
            if selection:
                self.session.pop(selection.uuid)
        self.mission.pop_task(self.uuid)
