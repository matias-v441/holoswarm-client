from __future__ import annotations

import numpy as np
import rclpy
import rerun as rr
import rerun.blueprint as rrb

from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, PointCloud2
from sensor_msgs_py import point_cloud2
from geometry_msgs.msg import PoseWithCovarianceStamped
from functools import partial
from typing import Any

POINT_CLOUDS = {
    "/uav14/open_vins/points_msckf": {
        "entity_path": "/world/pointclouds/msckf",
        "color": [100, 255, 120],       
        "radius": 0.015,
    },
    "/uav14/open_vins/points_slam": {
        "entity_path": "/world/pointclouds/slam",
        "color": [80, 180, 255],      
        "radius": 0.015,
    },
}

class RerunRosViewer(Node):
    def __init__(self) -> None:
        super().__init__("rerun_ros_viewer")

        self.bridge = CvBridge()

        self.image_subscription = self.create_subscription(
            Image,
            "/uav11/open_vins/trackhist",
            self.image_callback,
            qos_profile_sensor_data,
        )

        self.pcd_subscriptions: list[Any] = []

        for topic, config in POINT_CLOUDS.items():
            subscription = self.create_subscription(
                PointCloud2,
                topic,
                partial(
                    self.pointcloud_callback,
                    entity_path=config["entity_path"],
                    color=config["color"],
                    radius=config["radius"],
                ),
                qos_profile_sensor_data,
            )

            # Retain an explicit reference to every subscription.
            self.pcd_subscriptions.append(subscription)

            self.get_logger().info(
                f"Displaying {topic} as {config['entity_path']}"
            )

        self.poseimu_subscription = self.create_subscription(
            PoseWithCovarianceStamped,
            "/uav14/open_vins/poseimu",
            self.poseimu_callback,
            qos_profile_sensor_data,
        )

        rr.log(
            "/world/estimated_pose",
            rr.Arrows3D(
                vectors=[
                    [0.25, 0.0, 0.0],
                    [0.0, 0.25, 0.0],
                    [0.0, 0.0, 0.25],
                ],
                colors=[
                    [255, 0, 0],
                    [0, 255, 0],
                    [0, 0, 255],
                ],
            ),
            static=True,
        )


    @staticmethod
    def set_ros_time(msg) -> None:
        timestamp_ns = (
            msg.header.stamp.sec * 1_000_000_000
            + msg.header.stamp.nanosec
        )

        rr.set_time(
            "ros_time",
            timestamp=timestamp_ns / 1_000_000_000,
        )

    def poseimu_callback(self, msg: PoseWithCovarianceStamped) -> None:
        self.set_ros_time(msg)

        position = msg.pose.pose.position
        orientation = msg.pose.pose.orientation
        rr.log(
            "/world/estimated_pose",
            rr.Transform3D(
                translation=[position.x, position.y, position.z],
                rotation=rr.Quaternion(
                    xyzw=[
                        orientation.x,
                        orientation.y,
                        orientation.z,
                        orientation.w,
                    ]
                ),
            ),
        )

    def image_callback(self, msg: Image) -> None:
        self.set_ros_time(msg)

        # cv_bridge returns BGR by default; request RGB explicitly.
        frame_rgb = self.bridge.imgmsg_to_cv2(
            msg,
            desired_encoding="rgb8",
        )

        rr.log(
            "/camera/image",
            rr.Image(frame_rgb),
        )

    
    def pointcloud_callback(
        self,
        msg: PointCloud2,
        *,
        entity_path: str,
        color: list[int],
        radius: float,
    ) -> None:
        self.set_ros_time(msg)

        try:
            xyz = point_cloud2.read_points_numpy(
                msg,
                field_names=["x", "y", "z"],
                skip_nans=True,
            )
        except Exception as exc:
            self.get_logger().error(
                f"Failed reading {entity_path}: {exc}"
            )
            return

        xyz = np.asarray(xyz, dtype=np.float32).reshape(-1, 3)

        if xyz.size == 0:
            # Clear this entity when an empty cloud is received.
            rr.log(entity_path, rr.Clear(recursive=False))
            return

        rr.log(
            entity_path,
            rr.Points3D(
                positions=xyz,
                colors=color,
                radii=radius,
            ),
        )


def main() -> None:
    rr.init("ros2_camera_and_3d", spawn=True)

    rr.log(
        "/world",
        rr.ViewCoordinates.RIGHT_HAND_Z_UP,
        static=True,
    )

    rr.send_blueprint(
        rrb.Horizontal(
            rrb.Spatial2DView(
                name="Camera",
                origin="/camera",
            ),
            rrb.Spatial3DView(
                name="Point cloud",
                origin="/world",
            ),
            column_shares=[1, 1],
        )
    )

    rclpy.init()
    node = RerunRosViewer()

    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
