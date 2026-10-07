import json
import math
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from holoswarm_client.data.mission import Coverage, Mission, PointGlobal, PointLocal, Waypoints
from holoswarm_client.iroc.mission_codec import CodecError, decode_mission, encode_draft

TEST_DIR = Path(__file__).resolve().parents[1]
FIXTURES = sorted((TEST_DIR / "json" / "missions" / "holoswarm").glob("*.json")) + [
    TEST_DIR / "json" / "missions" / name
    for name in ("coverage.json", "coverage_teme.json", "heading.json", "one_drone.json",
                 "sequential_subtask.json", "two_drones.json", "waypoint.json")
]
HOMES = {name: (3.0, -2.0) for name in ("uav1", "uav11", "uav12", "uav14", "uav16")}


def close(a, b) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(close(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(close(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return math.isclose(a, b, abs_tol=1e-9)
    return a == b


def normalized_details(jrepr):
    details = json.loads(json.dumps(jrepr["details"]))
    if jrepr["type"] == "CoveragePlanner":  # export writes the default explicitly
        details.setdefault("target_subtask_count", 1)
    for robot in details.get("robots", ()):
        if isinstance(robot, dict):
            for point in robot["points"]:
                point.setdefault("heading", 0.0)
                point.pop("parallel_execution", None)
                for subtask in point.get("subtasks", ()):
                    if subtask["type"] == "gazebo_gimbal":  # export writes the defaults explicitly
                        subtask.setdefault("continue_without_waiting", False)
                        subtask.setdefault("stop_on_failure", False)
                        subtask.setdefault("max_retries", 1)
                        subtask.setdefault("retry_delay", 0.0)
    return details


class CodecRoundTrip(unittest.TestCase):

    def test_fixtures_round_trip(self):
        for path in FIXTURES:
            with self.subTest(path=path.name):
                original = json.loads(path.read_text())
                tasks = decode_mission(original, HOMES)
                encoded = encode_draft(tasks, (), HOMES)
                self.assertEqual(encoded["type"], original["type"])
                self.assertTrue(close(normalized_details(encoded), normalized_details(original)),
                                f"{encoded['details']} != {original['details']}")

    def test_local_points_are_shifted_by_home(self):
        original = json.loads((TEST_DIR / "json" / "missions" / "holoswarm" / "wp_14.json").read_text())
        (task,) = decode_mission(original, HOMES)
        first = original["details"]["robots"][0]["points"][0]
        self.assertIsInstance(task.points[0], PointLocal)
        self.assertAlmostEqual(task.points[0].position[0], first["x"] + 3.0)
        self.assertAlmostEqual(task.points[0].position[1], first["y"] - 2.0)

    def test_encoding_is_pure_and_stable(self):
        mission = Mission("uav1")
        mission.robot_homes = dict(HOMES)
        mission.from_json(json.loads((TEST_DIR / "json" / "missions" / "waypoint.json").read_text()))
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                first = mission.to_json("fixed-id")
                second = mission.to_json("fixed-id")
                self.assertEqual(os.listdir(tmp), [], "serialization must not write files")
            finally:
                os.chdir(cwd)
        self.assertEqual(first, second)
        self.assertEqual(first["uuid"], "fixed-id")

    def test_export_is_explicit(self):
        mission = Mission("uav1")
        mission.robot_homes = dict(HOMES)
        mission.from_json(json.loads((TEST_DIR / "json" / "missions" / "one_drone.json").read_text()))
        with tempfile.TemporaryDirectory() as tmp:
            path = mission.export_json(str(Path(tmp) / "out.json"))
            self.assertEqual(json.loads(Path(path).read_text())["type"], "WaypointPlanner")


class CodecValidation(unittest.TestCase):

    def path(self, robot, points):
        return Waypoints(points=tuple(points), time_interval=(0., 1.), assigned_robot=robot)

    def area(self, robots=("uav1",)):
        return Coverage(points=((50.0, 14.0), (50.1, 14.0), (50.1, 14.1)), time_interval=(0., 1.),
                        height_id=0, height=5.0, assigned_robots=robots)

    def test_mixed_content_is_rejected(self):
        with self.assertRaisesRegex(CodecError, "cannot be submitted as one mission"):
            encode_draft([self.area(), self.path("uav1", [PointLocal((0, 0, 2))])], ("uav1",), HOMES)

    def test_target_subtask_count_round_trips(self):
        area = replace(self.area(), target_subtask_count=3)
        payload = encode_draft([area], ("uav1",), HOMES)
        self.assertEqual(payload["details"]["target_subtask_count"], 3)
        (decoded,) = decode_mission(payload, HOMES)
        self.assertEqual(decoded.target_subtask_count, 3)
        self.assertEqual(encode_draft([self.area()], ("uav1",), HOMES)["details"]["target_subtask_count"], 1)

    def test_target_subtask_count_below_one_is_rejected(self):
        with self.assertRaisesRegex(CodecError, "sub-task count"):
            encode_draft([replace(self.area(), target_subtask_count=0)], ("uav1",), HOMES)

    def test_multiple_areas_are_rejected(self):
        with self.assertRaisesRegex(CodecError, "one coverage area"):
            encode_draft([self.area(), self.area()], ("uav1",), HOMES)

    def test_empty_is_rejected(self):
        with self.assertRaises(CodecError):
            encode_draft([], ("uav1",), HOMES)

    def test_global_points_need_no_home(self):
        payload = encode_draft([self.path("uav7", [PointGlobal(50.0, 14.0, 0, 5.0)])], ("uav7",), {})
        robot = payload["details"]["robots"][0]
        self.assertEqual((robot["name"], robot["frame_id"]), ("uav7", 1))
        self.assertEqual(robot["points"][0]["x"], 50.0)

    def test_local_points_need_a_home(self):
        with self.assertRaisesRegex(CodecError, "Home position of uav7"):
            encode_draft([self.path("uav7", [PointLocal((1, 2, 3))])], ("uav7",), {})

    def test_robot_is_required_and_unique(self):
        with self.assertRaisesRegex(CodecError, "no robot"):
            encode_draft([self.path(None, [PointGlobal(50.0, 14.0, 0, 5.0)])], (), {})
        with self.assertRaisesRegex(CodecError, "more than one path"):
            encode_draft([self.path("uav1", [PointGlobal(50.0, 14.0, 0, 5.0)]),
                          self.path("uav1", [PointGlobal(50.0, 14.0, 0, 5.0)])], ("uav1",), {})

    def test_multi_robot_routes_stay_one_mission(self):
        payload = encode_draft([self.path("uav1", [PointGlobal(50.0, 14.0, 0, 5.0)]),
                                self.path("uav2", [PointGlobal(50.0, 14.1, 0, 5.0)])], (), {})
        self.assertEqual([r["name"] for r in payload["details"]["robots"]], ["uav1", "uav2"])


if __name__ == "__main__":
    unittest.main()
