import unittest
from dataclasses import replace
from unittest import mock

from holoswarm_client.data.monitoring import (BatteryState, GeneralRobotInfo, GlobalPose, LocalPose, Monitoring, RobotState,
                                              StateEstimationInfo, Vector3, VectorPair)

ZERO = VectorPair(Vector3(0., 0., 0.), Vector3(0., 0., 0.))


def estimate(robot="uav1", x=1.0, lat=49.36, alt=312.5) -> StateEstimationInfo:
    return StateEstimationInfo(robot, "frame", 5.0, "gps", LocalPose(x, 2.0, 3.0, 0.0), GlobalPose(lat, 14.26, alt, 0.0),
                               ZERO, ZERO)


def battery(robot="uav1", percentage=0.8) -> GeneralRobotInfo:
    return GeneralRobotInfo(robot, "multirotor", BatteryState(15.9, percentage, 1.0), True)


class MonitoringTest(unittest.TestCase):

    def setUp(self):
        self.monitoring = Monitoring()
        self.clock = mock.patch("holoswarm_client.data.monitoring.time.time", return_value=100.0)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)

    def push(self, robot="uav1", **fields):
        current = self.monitoring.telemetry(robot) or RobotState(robot)
        self.monitoring.push(replace(current, **fields))

    def at(self, t):
        self.now.return_value = t

    def test_first_estimate_is_a_change(self):
        self.push(general_robot_info=battery())
        self.assertIsNone(self.monitoring.position_changed_at("uav1"), "no estimate yet")
        self.push(state_estimation_info=estimate())
        self.assertEqual(self.monitoring.position_changed_at("uav1"), 100.0)

    def test_same_position_does_not_count(self):
        self.push(state_estimation_info=estimate())
        self.at(105.0)
        self.push(state_estimation_info=estimate())
        self.push(general_robot_info=battery(percentage=0.5))
        self.push(state_estimation_info=replace(estimate(), current_estimator="other"))
        self.assertEqual(self.monitoring.position_changed_at("uav1"), 100.0)

    def test_any_position_change_counts(self):
        self.push(state_estimation_info=estimate())
        for t, changed in ((101.0, estimate(x=1.0 + 1e-12)), (102.0, estimate(lat=49.36 + 1e-12)), (103.0, estimate(alt=312.6))):
            self.at(t)
            self.push(state_estimation_info=changed)
            self.assertEqual(self.monitoring.position_changed_at("uav1"), t)

    def test_robots_are_discovered_sorted(self):
        self.assertEqual(self.monitoring.robot_names(), ())
        self.push("uav2", general_robot_info=battery("uav2"))
        self.push("uav1", state_estimation_info=estimate("uav1"))
        self.assertEqual(self.monitoring.robot_names(), ("uav1", "uav2"))

    def test_notify_only_after_a_push(self):
        seen = []
        self.monitoring.subscribe(seen.append)
        self.monitoring.notify()
        self.assertEqual(seen, [])
        self.push(state_estimation_info=estimate())
        self.monitoring.notify()
        self.monitoring.notify()
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].position_changed_at("uav1"), 100.0, "the copy carries the change times")
        self.assertEqual(seen[0].robot_names(), ("uav1",))


if __name__ == "__main__":
    unittest.main()
