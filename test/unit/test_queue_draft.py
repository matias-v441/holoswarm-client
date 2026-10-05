import unittest

from holoswarm_client.data.queue_draft import QueueDraft

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


if __name__ == "__main__":
    unittest.main()
