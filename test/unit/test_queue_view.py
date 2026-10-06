import unittest
from dataclasses import replace

from holoswarm_client.data.execution import QueueExecution
from holoswarm_client.data.mission import Coverage, Mission, PointGlobal, Waypoints
from holoswarm_client.data.queue_draft import QueueDraft
from holoswarm_client.data.queue_view import QueueView
from holoswarm_client.data.session import Selection, Session

AREA = {"type": "CoveragePlanner", "details": {"robots": ["uav1"], "search_area": [
    {"x": 49.36, "y": 14.26}, {"x": 49.361, "y": 14.26}, {"x": 49.361, "y": 14.261}], "height_id": 0, "height": 5.0,
    "terminal_action": 0}}


def route(*robots: str) -> dict:
    return {"type": "WaypointPlanner", "details": {"robots": [
        {"name": r, "frame_id": 1, "height_id": 0, "terminal_action": 0,
         "points": [{"x": 49.36, "y": 14.26, "z": 5.0, "heading": 0.0}, {"x": 49.362, "y": 14.262, "z": 5.0, "heading": 0.0}]}
        for r in robots]}}


def stored(state="CREATED", queue_id="q1") -> QueueExecution:
    return QueueExecution.from_json({"queue_id": queue_id, "scheduler": "batch", "state": state, "missions": [
        {"id": "m1", "name": "Routes", **route("uav1", "uav2")},
        {"id": "m2", "name": "Area", **AREA},
    ]})


class QueueViewTest(unittest.TestCase):

    def setUp(self):
        self.mission = Mission("uav1", "uav2")
        self.session = Session()
        self.draft = QueueDraft()
        self.focused = []
        self.view = QueueView(self.mission, self.session, self.draft, on_focus=self.focused.append)

    def load(self, queue=None):
        self.draft.load(queue or stored())
        self.draft.notify()

    def owners(self):
        return [t.mission_id for t in self.mission.tasks.values()]

    def task(self, mission_id, index=0):
        return self.mission.tasks_of(mission_id)[index]

    # | ----------------------- showing the queue ----------------------- |

    def test_the_whole_queue_is_shown(self):
        self.load()
        self.assertEqual(self.owners(), ["m1", "m1", "m2"], "one task per route, one per area, tagged with the mission")
        self.assertIsInstance(self.task("m2"), Coverage)
        self.assertEqual(len(self.focused[-1]), 3, "the map frames the whole queue")

    def test_loading_selects_the_first_mission_and_task(self):
        self.load()
        self.assertEqual(self.session.selected_mission, "m1")
        self.assertEqual(self.session.selected_task, self.task("m1").uuid)

    def test_loading_does_not_change_the_missions(self):
        self.load()
        self.assertFalse(self.draft.dirty)
        self.assertEqual(self.draft.problems, [])

    def test_opening_the_created_queue_selects_its_first_task(self):
        path = Waypoints(points=(PointGlobal(49.37, 14.27, 0, 5.0),), time_interval=(0., 1.), assigned_robot="uav1")
        self.mission.push_task(path)
        (new,) = self.draft.missions
        self.draft.notify()
        self.session.select_task(None)
        created = QueueExecution.from_json({"queue_id": "q9", "scheduler": "batch", "state": "CREATED",
                                            "missions": [dict(new.to_wire())]})
        self.draft.load(created)  # the same mission id as in the new queue
        self.draft.notify()
        self.assertEqual(self.session.selected_mission, new.mission_id)
        self.assertEqual(self.session.selected_task, self.task(new.mission_id).uuid)

    def test_renamed_robots_are_shown(self):
        self.load()
        self.session.select_mission("m2")
        self.draft.choose_robot("uav2", "uav3")
        self.draft.notify()
        self.assertEqual(sorted(t.assigned_robot for t in self.mission.tasks_of("m1")), ["uav1", "uav3"])
        self.assertEqual(self.task("m2").assigned_robots, ("uav1",), "uav2 was not in the area")
        self.assertEqual(self.session.selected_mission, "m2")
        self.assertEqual(self.session.selected_task, self.task("m2").uuid, "a task of the mission stays selected")
        self.assertEqual(self.draft.problems, [])

    def test_closing_shows_the_new_queue(self):
        self.load()
        self.draft.close()
        self.draft.notify()
        self.assertEqual(self.mission.tasks, {})
        self.assertIsNone(self.session.selected_mission)
        self.assertIsNone(self.session.selected_task)

    # | ----------------------- selection ----------------------- |

    def test_selecting_a_task_selects_its_mission(self):
        self.load()
        area = self.task("m2")
        self.session.push(replace(self.session.selection, items=(area.uuid,)))  # Ctrl+click on the map
        self.assertEqual(self.session.selected_mission, "m2")
        self.assertEqual(self.session.selected_task, area.uuid)

    def test_selecting_a_mission_selects_its_first_task(self):
        self.load()
        self.session.select_mission("m2")
        self.assertEqual(self.session.selected_task, self.task("m2").uuid)
        self.session.select_mission("m1")
        self.assertEqual(self.session.selected_task, self.task("m1").uuid)

    def test_clearing_the_task_keeps_the_mission(self):
        self.load()
        self.session.select_mission("m2")
        self.session.pop(self.session.selection.uuid)  # plain click on empty map
        self.assertEqual(self.session.selected_mission, "m2")
        self.assertIsNone(self.session.selected_task, "a new task can be drawn in the mission")

    def test_removed_mission_selects_the_first(self):
        self.load()
        self.session.select_mission("m2")
        self.draft.remove("m2")
        self.draft.notify()
        self.assertEqual(self.session.selected_mission, "m1")
        self.assertEqual(self.owners(), ["m1", "m1"])

    def test_focus_mission(self):
        self.load()
        self.view.focus_mission("m2")
        self.assertEqual(self.focused[-1], (self.task("m2"),))

    # | ----------------------- live editing ----------------------- |

    def test_editing_a_task_re_encodes_its_mission(self):
        self.load()
        area = self.task("m2")
        uuids = set(self.mission.tasks)
        self.mission.push_task(replace(area, height=12.5))
        self.assertEqual(self.draft.mission("m2").payload["details"]["height"], 12.5)
        self.assertTrue(self.draft.dirty)
        self.draft.notify()
        self.assertEqual(set(self.mission.tasks), uuids, "the tasks (and the selection) stay")

    def test_other_missions_are_not_rewritten(self):
        self.load()
        before = self.draft.mission("m1").payload
        self.mission.push_task(replace(self.task("m2"), height=12.5))
        self.assertIs(self.draft.mission("m1").payload, before)

    def test_a_mission_that_does_not_encode_gets_an_error(self):
        self.load()
        area = self.task("m2")
        good = self.draft.mission("m2").payload
        self.mission.push_task(replace(area, points=area.points[:2]))
        self.assertIn("3 points", self.draft.error("m2"))
        self.assertEqual([m.mission_id for m, _ in self.draft.problems], ["m2"])
        self.assertIs(self.draft.mission("m2").payload, good, "the last good payload is kept")
        self.mission.push_task(area)
        self.assertIsNone(self.draft.error("m2"))
        self.assertEqual(self.draft.problems, [])

    def test_deleting_the_last_task(self):
        self.load()
        self.mission.pop_task(self.task("m2").uuid)
        self.assertTrue(self.draft.error("m2"))
        self.assertEqual(self.session.selected_mission, "m1", "the selected mission was not touched")

    def drawn_path(self, robot=None):
        return Waypoints(points=(PointGlobal(49.37, 14.27, 0, 5.0),), time_interval=(0., 1.), assigned_robot=robot)

    def drawn_area(self):
        return Coverage(points=((49.37, 14.27),), time_interval=(0., 1.), height_id=0, height=5.0)

    def draw_in(self, mission_id, task):
        self.session.select_mission(mission_id)
        self.session.select_task(None)  # Ctrl+click on empty map draws a new task
        self.mission.push_task(task)
        return self.mission.tasks[task.uuid]

    def test_a_new_path_joins_the_selected_mission_with_a_free_robot(self):
        self.load(QueueExecution.from_json({"queue_id": "q1", "scheduler": "batch", "state": "CREATED", "missions": [
            {"id": "m1", "name": "Route", **route("uav1")}]}))
        placed = self.draw_in("m1", self.drawn_path())
        self.assertEqual((placed.mission_id, placed.assigned_robot), ("m1", "uav2"))
        self.assertEqual(len(self.draft.missions), 1)
        self.assertEqual([r["name"] for r in self.draft.mission("m1").payload["details"]["robots"]], ["uav1", "uav2"])
        self.assertIsNone(self.draft.error("m1"))

    def test_a_task_that_does_not_fit_starts_a_new_mission(self):
        cases = {
            "path, every robot has one": ("m1", self.drawn_path),
            "path of a robot that has one": ("m1", lambda: self.drawn_path("uav1")),
            "area into routes": ("m1", self.drawn_area),
            "second area": ("m2", self.drawn_area),
            "path into an area": ("m2", self.drawn_path),
        }
        for name, (target, make) in cases.items():
            with self.subTest(name):
                self.load()
                placed = self.draw_in(target, make())
                new = self.draft.missions[-1]
                self.assertEqual(len(self.draft.missions), 3)
                self.assertEqual(placed.mission_id, new.mission_id)
                self.assertEqual(new.name, "Mission 3")
                self.assertEqual(self.session.selected_mission, new.mission_id, "the new mission is selected")
                self.assertEqual(len(self.mission.tasks_of(target)), {"m1": 2, "m2": 1}[target], "nothing joined it")
                self.assertIsNone(self.draft.error(target), "the selected mission is untouched")
                self.assertFalse(self.draft.mission(target).empty)

    def test_a_new_area_in_an_empty_mission(self):
        self.load()
        new = self.draft.add_empty()
        placed = self.draw_in(new.mission_id, self.drawn_area())
        self.assertEqual(placed.mission_id, new.mission_id)
        self.assertEqual(len(self.draft.missions), 3, "no other mission was started")

    def test_new_mission(self):
        self.load()
        new = self.draft.add_empty()
        self.session.select_mission(new.mission_id)
        self.assertIsNone(self.session.selected_task)
        self.assertEqual(self.draft.problems[0][0].mission_id, new.mission_id, "empty until a path is drawn")
        self.draft.notify()
        path = Waypoints(points=(PointGlobal(49.37, 14.27, 0, 5.0),), time_interval=(0., 1.), assigned_robot="uav2")
        self.mission.push_task(path)
        self.session.push(Selection(uuid=self.session.selection.uuid if self.session.selection else "s", items=(path.uuid,)))
        self.assertEqual(self.draft.mission(new.mission_id).planner_type, "WaypointPlanner")
        self.assertEqual(self.draft.problems, [])
        self.assertEqual(self.session.selected_mission, new.mission_id)

    def test_drawing_in_an_empty_queue_starts_a_mission(self):
        area = Coverage(points=((49.36, 14.26),), time_interval=(0., 1.), height_id=0, height=5.0)
        self.mission.push_task(area)
        (mission,) = self.draft.missions
        self.assertEqual(self.mission.tasks[area.uuid].mission_id, mission.mission_id)
        self.assertEqual(self.session.selected_mission, mission.mission_id)
        self.assertIn("3 points", self.draft.error(mission.mission_id))
        for lat, lon in ((49.361, 14.26), (49.361, 14.261)):
            area = self.mission.tasks[area.uuid]
            self.mission.push_task(replace(area, points=(*area.points, (lat, lon))))
        self.assertEqual(self.draft.problems, [])
        self.assertEqual(self.draft.missions[0].planner_type, "CoveragePlanner")

    def test_reloaded_mission_is_shown_again(self):
        self.load()
        old = self.task("m2").uuid
        self.load(stored())  # e.g. reverted
        self.assertNotIn(old, self.mission.tasks)
        self.assertEqual(self.owners(), ["m1", "m1", "m2"])

    # | ----------------------- read-only ----------------------- |

    def test_a_submitted_queue_is_read_only(self):
        self.load(stored("RUNNING"))
        self.assertTrue(self.mission.read_only)
        area = self.task("m2")
        self.mission.push_task(replace(area, height=12.5))
        self.mission.pop_task(area.uuid)
        self.mission.push_task(Waypoints(points=(PointGlobal(49.37, 14.27, 0, 5.0),), time_interval=(0., 1.)))
        self.assertEqual(self.mission.tasks_of("m2"), (area,))
        self.assertEqual(len(self.mission.tasks), 3)
        self.assertFalse(self.draft.dirty)
        self.session.push(replace(self.session.selection, items=("not-added",)))
        self.assertEqual(self.session.selected_task, None, "a selection of nothing is dropped")

    def test_read_only_follows_the_state(self):
        self.load()
        self.draft.sync(stored("READY"))
        self.draft.notify()
        self.assertTrue(self.mission.read_only)
        self.draft.sync(stored("CANCELLED"))
        self.draft.notify()
        self.assertFalse(self.mission.read_only)


if __name__ == "__main__":
    unittest.main()
