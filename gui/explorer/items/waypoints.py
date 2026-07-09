import dearpygui.dearpygui as dpg
from data.mission import *
from data.session import *
from dataclasses import replace

class WaypointsNode:
    def __init__(self, mission: Mission, session: Session, uuid: str, tag: str, parent_tag: str):
        self.mission = mission
        self.session = session
        self.path_uuid = uuid
        self.tag = tag
        self.parent_tag = parent_tag
    
    def delete(self):
        if dpg.does_item_exist(self.tag):
            dpg.delete_item(self.tag)

    def draw(self):
        if self.path_uuid not in self.mission.waypoints:
            self.delete()
        wp: Waypoints = self.mission.waypoints[self.path_uuid]
        with dpg.tree_node(tag=self.tag, label=f"Path {self.path_uuid}", parent=self.parent_tag, default_open=True):
            dpg.add_button(label="Delete", callback=self._delete_path)
            dpg.add_combo(
                items=list(self.mission.robot_names),
                default_value=wp.assigned_robot or "",
                width=120,
                callback=self._on_assigned_robot_changed,
            )
            for index, point in enumerate(wp.points):
                label = f"Point {index}"
                with dpg.tree_node(tag=f"{self.tag}_point_{index}", label=label, default_open=True):
                    if isinstance(point, PointLocal):
                        x, y, z = point.position
                        dpg.add_input_float(label="x", default_value=x, width=120,
                                            callback=self._on_local_position_changed, user_data=(index, 0), on_enter=True)
                        dpg.add_input_float(label="y", default_value=y, width=120,
                                            callback=self._on_local_position_changed, user_data=(index, 1), on_enter=True)
                        dpg.add_input_float(label="z", default_value=z, width=120,
                                            callback=self._on_local_position_changed, user_data=(index, 2), on_enter=True)
                        dpg.add_input_float(label="heading", default_value=point.heading, width=120,
                                            callback=self._on_heading_changed, user_data=index, on_enter=True)
                    elif isinstance(point, PointGlobal):
                        dpg.add_input_float(label="lat", default_value=point.lat, width=120,
                                            callback=self._on_global_coordinate_changed, user_data=(index, "lat"), on_enter=True)
                        dpg.add_input_float(label="lon", default_value=point.lon, width=120,
                                            callback=self._on_global_coordinate_changed, user_data=(index, "lon"), on_enter=True)
                        dpg.add_input_text(label="height id", default_value=str(point.height_id), width=120,
                                            callback=self._on_height_id_changed, user_data=index, on_enter=True)
                        dpg.add_input_float(label="height", default_value=point.height, width=120,
                                            callback=self._on_global_coordinate_changed, user_data=(index, "height"), on_enter=True)
                        dpg.add_input_float(label="heading", default_value=point.heading, width=120,
                                            callback=self._on_heading_changed, user_data=index, on_enter=True)

                    with dpg.tree_node(label="Subtasks"):
                        for subtask_index, subtask in enumerate(point.subtasks):
                            dpg.add_text(f"{subtask_index + 1}. {type(subtask).__name__}: {subtask.parameter}")
                        dpg.add_button(label="Add wait", callback=self._add_wait_subtask, user_data=index)

    def _delete_path(self, sender=None, app_data=None, user_data=None) -> None:
        if self.session.item_selected(self.path_uuid):
            selection = self.session.selection
            if selection:
                self.session.pop(selection.uuid)
        self.mission.pop_task(self.path_uuid)

    def _on_assigned_robot_changed(self, sender, app_data, user_data=None) -> None:
        wp: Waypoints = self.mission.waypoints[self.path_uuid]
        self.mission.push_task(replace(wp, assigned_robot=app_data))
    
    def _replace_point(self, point_index: int, point: PointLocal | PointGlobal) -> None:
        wp: Waypoints = self.mission.waypoints[self.path_uuid]
        points = list(wp.points)
        points[point_index] = point
        self.mission.push_task(replace(wp, points=tuple(points)))

    def _on_local_position_changed(self, sender, app_data, user_data) -> None:
        point_index, coordinate_index = user_data
        point = self.mission.waypoints[self.path_uuid].points[point_index]
        if not isinstance(point, PointLocal):
            return
        position = list(point.position)
        position[coordinate_index] = float(app_data)
        self._replace_point(point_index, replace(point, position=tuple(position)))

    def _on_global_coordinate_changed(self, sender, app_data, user_data) -> None:
        point_index, coordinate_name = user_data
        point = self.mission.waypoints[self.path_uuid].points[point_index]
        if isinstance(point, PointGlobal):
            self._replace_point(point_index, replace(point, **{coordinate_name: float(app_data)}))

    def _on_height_id_changed(self, sender, app_data, point_index: int) -> None:
        point = self.mission.waypoints[self.path_uuid].points[point_index]
        if not isinstance(point, PointGlobal):
            return
        height_id = int(app_data) if app_data.isdecimal() else app_data
        self._replace_point(point_index, replace(point, height_id=height_id))

    def _on_heading_changed(self, sender, app_data, point_index: int) -> None:
        point = self.mission.waypoints[self.path_uuid].points[point_index]
        self._replace_point(point_index, replace(point, heading=float(app_data)))

    def _add_wait_subtask(self, sender, app_data, point_index: int) -> None:
        point = self.mission.waypoints[self.path_uuid].points[point_index]
        self._replace_point(point_index, replace(point, subtasks=(*point.subtasks, SubtaskWait(0.))))
