import dearpygui.dearpygui as dpg
from math import cos, sin

from queue import SimpleQueue, Empty

from holoswarm_client.data.monitoring import *
from holoswarm_client.gui.map.map import Map

class Fleet:

    def __init__(self, map: Map, drawlist_tag: str = "map_grid_drawlist", use_local_poses: bool = False):
        self.map = map
        self.drawlist_tag = drawlist_tag
        self.use_local_poses = use_local_poses
        self.poses: dict[str, LocalPose | GlobalPose] = {}
        self.homes: dict[str, LocalPose] = {}
        self.items: set[int | str] = set()
        map.monitoring.subscribe(self.monitoring_callback)
        self._ui_events = SimpleQueue()

    def monitoring_callback(self, monitoring: Monitoring) -> None:
        pose_attribute = "local_pose" if self.use_local_poses else "global_pose"
        old_poses = self.poses
        self.poses = {
            robot_name: getattr(state.state_estimation_info, pose_attribute)
            for robot_name, state in monitoring._robot_states.items()
            if state.state_estimation_info is not None
        }
        if not old_poses:
            if self.use_local_poses:
                self.homes = self.poses.copy()
            else:
                latlon_to_world = self.map.latlon_to_world
                self.homes = {
                    robot_name: LocalPose(
                        *latlon_to_world(pose.latitude, pose.longitude),
                        pose.altitude,
                        pose.heading,
                    )
                    for robot_name, pose in self.poses.items()
                }
            self.map.mission.robot_homes = {
                robot_name: (home.x, home.y)
                for robot_name, home in self.homes.items()
            }
        self._ui_events.put(self.draw)

    def process_events(self):
        while True:
            try:
                event = self._ui_events.get_nowait()
            except Empty:
                break
            event.__call__()

    def draw(self):

        for item in self.items:
            if dpg.does_item_exist(item):
                dpg.delete_item(item)
        self.items.clear()

        if not dpg.does_item_exist(self.drawlist_tag):
            return

        size = 14.0
        for robot_name, pose in self.poses.items():
            if self.use_local_poses:
                if pose.x is None or pose.y is None or pose.heading is None:
                    continue
                position = (pose.x, pose.y)
            else:
                if pose.latitude is None or pose.longitude is None or pose.heading is None:
                    continue
                position = self.map.latlon_to_world(pose.latitude, pose.longitude)
            center = self.map.world_to_canvas(position)
            direction = (cos(pose.heading), -sin(pose.heading))
            normal = (-direction[1], direction[0])
            back = (
                center[0] - direction[0] * size * 0.55,
                center[1] - direction[1] * size * 0.55,
            )
            points = [
                (
                    center[0] + direction[0] * size,
                    center[1] + direction[1] * size,
                ),
                (
                    back[0] + normal[0] * size * 0.55,
                    back[1] + normal[1] * size * 0.55,
                ),
                (
                    back[0] - normal[0] * size * 0.55,
                    back[1] - normal[1] * size * 0.55,
                ),
            ]

            self.items.update({
                dpg.draw_polygon(
                    points,
                    color=(255, 255, 255, 255),
                    fill=(55, 185, 255, 255),
                    thickness=2,
                    parent=self.drawlist_tag,
                ),
                dpg.draw_circle(
                    center,
                    size * 0.18,
                    color=(42, 44, 50, 255),
                    fill=(42, 44, 50, 255),
                    parent=self.drawlist_tag,
                ),
                dpg.draw_text(
                    (center[0] + size * 0.7, center[1] - size * 0.7),
                    robot_name,
                    color=(230, 232, 238, 255),
                    parent=self.drawlist_tag,
                ),
            })
