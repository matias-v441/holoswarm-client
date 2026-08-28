import cv2
import rclpy
import rerun as rr
import rerun.blueprint as rrb

from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image


CAMERAS = {
    #"/uav11/rgb/image_raw": "video/raw",
    #"/uav11/rgbd/infra1/image_rect_raw": "video/raw",
    "/uav11/realsense/infra1/image_rect_raw": "video/raw",
    #"/uav11/open_vins/trackhist": "video/vins",
}


class RerunCameraViewer(Node):
    def __init__(self) -> None:
        super().__init__("rerun_camera_viewer")
        self.bridge = CvBridge()

        for topic, entity_path in CAMERAS.items():
            self.create_subscription(
                Image,
                topic,
                lambda msg, path=entity_path: self.on_image(msg, path),
                qos_profile_sensor_data,
            )

    def on_image(self, msg: Image, entity_path: str) -> None:
        frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding="rgb8")

        timestamp_ns = (
            msg.header.stamp.sec * 1_000_000_000
            + msg.header.stamp.nanosec
        )

        rr.set_time("ros_time", timestamp=timestamp_ns / 1e9)
        rr.log(entity_path, rr.Image(frame))


def main() -> None:
    rr.init("ros2_multicamera", spawn=True)

    blueprint = rrb.Blueprint(
        rrb.Grid(
            rrb.Spatial2DView(
                name="Raw",
                origin="video/raw",
            ),
            rrb.Spatial2DView(
                name="Vins",
                origin="video/vins",
            ),
        )
    )

    rr.send_blueprint(blueprint)

    rclpy.init()
    node = RerunCameraViewer()

    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
