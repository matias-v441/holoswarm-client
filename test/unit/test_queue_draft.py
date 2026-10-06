import unittest

from holoswarm_client.data.execution import QueueExecution
from holoswarm_client.data.queue_draft import DraftMission, QueueDraft, QueueSettings

COVERAGE = {"type": "CoveragePlanner", "details": {"robots": ["uav1"], "search_area": []}}
WAYPOINTS = {"type": "WaypointPlanner", "details": {"robots": [{"name": "uav1", "points": []}]}}


class QueueDraftTest(unittest.TestCase):

    def setUp(self):
        self.draft = QueueDraft()
        self.a = self.draft.add(COVERAGE, "area")
        self.b = self.draft.add(WAYPOINTS, "route")
        self.c = self.draft.add(COVERAGE, name="Custom")

    def ids(self):
        return [m.mission_id for m in self.draft.missions]

    def test_add(self):
        self.assertEqual(len(self.draft), 3)
        self.assertEqual([m.name for m in self.draft.missions], ["Mission 1", "Mission 2", "Custom"])
        self.assertEqual(len(set(self.ids())), 3)
        self.assertEqual(self.b.planner_type, "WaypointPlanner")

    def test_wire_format(self):
        self.assertEqual(self.a.to_wire(), {"id": self.a.mission_id, "name": "Mission 1", **COVERAGE})

    def test_move(self):
        self.draft.move(self.c.mission_id, -1)
        self.assertEqual(self.ids(), [self.a.mission_id, self.c.mission_id, self.b.mission_id])
        self.draft.move(self.a.mission_id, -1)  # already first
        self.draft.move(self.b.mission_id, 5)   # already last
        self.assertEqual(self.ids(), [self.a.mission_id, self.c.mission_id, self.b.mission_id])

    def test_remove_rename_clear(self):
        self.draft.rename(self.b.mission_id, "Route")
        self.assertEqual(self.draft.missions[1].name, "Route")
        self.draft.remove(self.a.mission_id)
        self.assertEqual(self.ids(), [self.b.mission_id, self.c.mission_id])
        self.draft.clear({self.b.mission_id})
        self.assertEqual(self.ids(), [self.c.mission_id])
        self.draft.clear()
        self.assertEqual(len(self.draft), 0)
        self.assertEqual(self.draft.add(COVERAGE).name, "Mission 4", "names keep counting")

    def test_notify(self):
        calls = []
        self.draft.subscribe(lambda _: calls.append(1))
        self.draft.notify()
        self.assertEqual(len(calls), 1)
        self.draft.rename(self.a.mission_id, "typed")
        self.draft.notify()
        self.assertEqual(len(calls), 1, "renaming does not redraw the editor being typed in")
        self.draft.move(self.b.mission_id, 1)
        self.draft.notify()
        self.assertEqual(len(calls), 2)


def stored(name="Stored", missions=None, state="FINISHED", **queue) -> QueueExecution:
    missions = missions if missions is not None else [
        {"id": "s1", "name": "Area", "priority": 2, "return_home_policy": 1, "state": "SUCCEEDED", "progress": 1.0, **COVERAGE},
        {"id": "s2", "name": "Route", "state": "FAILED", **WAYPOINTS},
    ]
    return QueueExecution.from_json({"queue_id": "q1", "name": name, "scheduler": "fleet", "params": {"assignment_policy": "fifo"},
                                     "world_id": "w1", "state": state, "missions": missions, **queue})


class QueueEditTest(unittest.TestCase):

    def setUp(self):
        self.draft = QueueDraft()
        self.new = self.draft.add(COVERAGE, "area")
        self.draft.set_settings(name="Next", scheduler="batch")

    def ids(self):
        return [m.mission_id for m in self.draft.missions]

    def test_load_copies_the_stored_queue(self):
        self.draft.load(stored())
        self.assertTrue(self.draft.editing)
        self.assertEqual(self.draft.queue_id, "q1")
        self.assertEqual(self.ids(), ["s1", "s2"])
        self.assertEqual(self.draft.settings, QueueSettings("Stored", "fleet", {"assignment_policy": "fifo"}, "w1"))
        self.assertFalse(self.draft.dirty)
        self.assertEqual(self.draft.missions[0].to_wire(),
                         {"id": "s1", "name": "Area", "priority": 2, "return_home_policy": 1, **COVERAGE},
                         "a stored mission goes back as it came")
        self.assertTrue(self.draft.missions[0].summary.startswith("Coverage"))

    def test_close_brings_back_the_new_queue(self):
        self.draft.load(stored())
        self.draft.add(WAYPOINTS)
        self.draft.set_settings(name="changed")
        self.draft.close()
        self.assertFalse(self.draft.editing)
        self.assertEqual(self.ids(), [self.new.mission_id])
        self.assertEqual(self.draft.settings.name, "Next")
        self.assertFalse(self.draft.dirty, "a new queue has nothing to upload")

    def test_every_change_makes_it_dirty(self):
        changes = {
            "rename": lambda: self.draft.rename("s1", "x"),
            "move": lambda: self.draft.move("s2", -1),
            "remove": lambda: self.draft.remove("s1"),
            "add": lambda: self.draft.add(WAYPOINTS),
            "update": lambda: self.draft.update("s2", COVERAGE, "area"),
            "settings": lambda: self.draft.set_settings(params={}),
        }
        for name, change in changes.items():
            with self.subTest(name):
                self.draft.load(stored())
                change()
                self.assertTrue(self.draft.dirty)
                self.draft.load(stored())  # revert
                self.assertFalse(self.draft.dirty)

    def test_new_missions_are_named_after_the_stored_ones(self):
        self.draft.load(stored())
        self.assertEqual(self.draft.add(WAYPOINTS).name, "Mission 3")

    def test_sync_follows_the_server_without_local_changes(self):
        self.draft.load(stored())
        self.draft.sync(stored(name="Renamed elsewhere", state="CREATED"))
        self.assertEqual(self.draft.settings.name, "Renamed elsewhere")
        self.assertFalse(self.draft.dirty or self.draft.outdated)

    def test_sync_rebases_on_the_uploaded_queue(self):
        self.draft.load(stored())
        self.draft.rename("s1", "Mine")
        uploaded = stored(missions=[{"id": "s1", "name": "Mine", "priority": 2, "return_home_policy": 1, **COVERAGE},
                                    {"id": "s2", "name": "Route", **WAYPOINTS}], state="CREATED")
        self.draft.sync(uploaded)
        self.assertFalse(self.draft.dirty)
        self.assertFalse(self.draft.outdated)

    def test_sync_keeps_local_changes(self):
        self.draft.load(stored())
        self.draft.rename("s1", "Mine")
        self.draft.sync(stored(name="Theirs"))
        self.assertEqual(self.draft.missions[0].name, "Mine")
        self.assertEqual(self.draft.settings.name, "Stored")
        self.assertTrue(self.draft.dirty)
        self.assertTrue(self.draft.outdated)

    def test_sync_ignores_copies_older_than_the_upload(self):
        self.draft.load(stored(updated_at=10.0))
        self.draft.rename("s1", "Mine")
        uploaded = stored(missions=[{"id": "s1", "name": "Mine", "priority": 2, "return_home_policy": 1, **COVERAGE},
                                    {"id": "s2", "name": "Route", **WAYPOINTS}], state="CREATED", updated_at=20.0)
        self.draft.sync(uploaded)  # the response of the upload
        self.draft.sync(stored(updated_at=10.0))  # the store has not seen the upload's event yet
        self.assertEqual(self.draft.missions[0].name, "Mine")
        self.assertFalse(self.draft.dirty or self.draft.outdated)

    def test_sync_ignores_state_and_other_queues(self):
        self.draft.load(stored())
        self.draft.rename("s1", "Mine")
        self.draft.sync(stored(state="CREATED"))  # same content, new run state
        self.assertFalse(self.draft.outdated)
        other = QueueExecution.from_json({"queue_id": "q2", "missions": []})
        self.draft.sync(other)
        self.assertEqual(self.draft.queue_id, "q1")

    def test_empty_missions_and_errors_are_problems(self):
        self.draft.load(stored())
        self.assertEqual(self.draft.problems, [])
        empty = self.draft.add_empty()
        self.assertTrue(empty.empty)
        self.assertEqual(empty.name, "Mission 3")
        self.draft.set_error("s1", "Only one coverage area")
        self.assertEqual([(m.mission_id, text) for m, text in self.draft.problems],
                         [("s1", "Only one coverage area"), (empty.mission_id, "no path or area yet")])
        self.draft.set_error("s1", None)
        self.draft.remove(empty.mission_id)
        self.assertEqual(self.draft.problems, [])
        self.draft.set_error("s2", "broken")
        self.draft.remove("s2")
        self.assertIsNone(self.draft.error("s2"), "a removed mission takes its error along")

    def test_problems_belong_to_their_queue(self):
        self.draft.set_error(self.new.mission_id, "broken")
        self.draft.load(stored())
        self.assertEqual(self.draft.problems, [])
        self.draft.close()
        self.assertEqual(self.draft.error(self.new.mission_id), "broken")

    def test_read_only_while_submitted(self):
        self.assertFalse(self.draft.read_only, "the new queue")
        self.draft.load(stored(state="RUNNING"))
        self.assertTrue(self.draft.read_only)
        self.draft.sync(stored(state="CANCELLED"))
        self.assertFalse(self.draft.read_only)
        self.draft.sync(stored(state="SUBMITTING"))
        self.assertTrue(self.draft.read_only)
        self.draft.close()
        self.assertFalse(self.draft.read_only)

    def test_mission_of_a_stored_queue(self):
        mission = DraftMission.of(stored().missions["s2"])
        self.assertEqual((mission.mission_id, mission.name, dict(mission.payload)), ("s2", "Route", WAYPOINTS))
        self.assertEqual(mission.to_wire(), {"id": "s2", "name": "Route", **WAYPOINTS})



def routes(*robots):
    return {"type": "WaypointPlanner", "details": {"robots": [{"name": r, "frame_id": 1, "points": []} for r in robots]}}


def area(*robots):
    return {"type": "CoveragePlanner", "details": {"robots": list(robots), "search_area": [], "height": 5.0}}


class RobotSubstitutionTest(unittest.TestCase):

    def setUp(self):
        self.draft = QueueDraft()
        self.draft.load(QueueExecution.from_json({"queue_id": "q1", "scheduler": "batch", "state": "CREATED", "missions": [
            {"id": "a", **routes("uav14", "uav16")},
            {"id": "b", **area("uav16")},
            {"id": "c", "type": "OtherPlanner", "details": {"robots": ["x"]}},
        ]}))

    def robots_of(self, mission_id):
        details = self.draft.mission(mission_id).payload["details"]
        return [r["name"] if isinstance(r, dict) else r for r in details["robots"]]

    def test_distinct_robots_of_the_queue(self):
        self.assertEqual(self.draft.robots, ("uav14", "uav16"))

    def test_substitution_renames_the_robot_in_every_mission(self):
        self.assertIsNone(self.draft.choose_robot("uav16", "uav2"))
        self.assertEqual(self.robots_of("a"), ["uav14", "uav2"])
        self.assertEqual(self.robots_of("b"), ["uav2"])
        self.assertEqual(self.draft.robots, ("uav14", "uav2"))
        self.assertIn("uav2", self.draft.mission("b").summary)
        self.assertEqual(self.draft.mission("a").payload["details"]["robots"][1]["frame_id"], 1, "the rest is kept")
        self.assertTrue(self.draft.dirty)

    def test_duplicates_are_refused_until_resolved(self):
        before = [m.payload for m in self.draft.missions]
        conflict = self.draft.choose_robot("uav14", "uav16")
        self.assertIn("duplicates are not allowed", conflict)
        self.assertEqual(self.draft.robot_conflict, conflict)
        self.assertEqual([m.payload for m in self.draft.missions], before, "nothing changed")
        self.assertEqual(self.draft.robot_choice("uav14"), "uav16", "the choice waits")
        self.assertFalse(self.draft.dirty)

        self.assertIsNone(self.draft.choose_robot("uav14", "uav14"), "taking it back resolves it")
        self.assertIsNone(self.draft.robot_conflict)
        self.assertEqual([m.payload for m in self.draft.missions], before)

    def test_swap(self):
        self.assertIsNotNone(self.draft.choose_robot("uav14", "uav16"))
        self.assertIsNone(self.draft.choose_robot("uav16", "uav14"))
        self.assertEqual(self.robots_of("a"), ["uav16", "uav14"])
        self.assertEqual(self.robots_of("b"), ["uav14"])
        self.assertIsNone(self.draft.robot_conflict)

    def test_pending_choice_of_a_removed_robot_is_dropped(self):
        self.draft.choose_robot("uav14", "uav16")
        self.draft.remove("a")
        self.assertIsNone(self.draft.robot_conflict, "uav14 is no longer in the queue")

    def test_choices_belong_to_their_queue(self):
        self.draft.choose_robot("uav14", "uav16")
        self.draft.close()
        self.assertIsNone(self.draft.robot_conflict)


if __name__ == "__main__":
    unittest.main()
