import unittest

from holoswarm_client.data.workspace import Workspace, border_latlon, same_area

FIELD_1 = [(49.362139, 14.260444), (49.363417, 14.260139), (49.363417, 14.262028), (49.361972, 14.260750)]
FIELD_2 = [(49.361972, 14.260750), (49.363417, 14.262028), (49.362889, 14.263111), (49.361694, 14.261833)]


def world_json(name, area, origin=None):
    return {
        "name": name,
        "origin": {"lat": origin[0], "lon": origin[1]} if origin else None,
        "min_z": 1.0,
        "max_z": 15.0,
        "safety_area": [{"lat": lat, "lon": lon} for lat, lon in area],
    }


def border_json(area, frame_id=1):
    return {"points": [{"x": lat, "y": lon} for lat, lon in area], "frame_id": frame_id, "height_id": 0}


WORKSPACE = Workspace.from_json({
    "name": "temesvar",
    "description": "test",
    "worlds": [
        world_json("field_1", FIELD_1, origin=(49.36275, 14.260778)),
        world_json("field_1_copy", FIELD_1, origin=(49.3627, 14.2607)),
        world_json("field_2", FIELD_2),
    ],
})


class SameAreaTest(unittest.TestCase):
    def test_identical(self):
        self.assertTrue(same_area(FIELD_1, FIELD_1))

    def test_other_start_vertex_direction_and_closing_vertex(self):
        rotated = FIELD_1[2:] + FIELD_1[:2]
        self.assertTrue(same_area(FIELD_1, rotated))
        self.assertTrue(same_area(FIELD_1, rotated[::-1]))
        self.assertTrue(same_area(FIELD_1, FIELD_1 + [FIELD_1[0]]))

    def test_rounding_is_tolerated(self):
        self.assertTrue(same_area(FIELD_1, [(lat + 1e-7, lon - 1e-7) for lat, lon in FIELD_1]))

    def test_different_areas(self):
        self.assertFalse(same_area(FIELD_1, FIELD_2))
        self.assertFalse(same_area(FIELD_1, FIELD_1[:3]))
        moved = [(lat + 1e-4, lon) for lat, lon in FIELD_1]  # ~11 m north
        self.assertFalse(same_area(FIELD_1, moved))
        # same vertices, but another polygon (a different order)
        self.assertFalse(same_area(FIELD_1, [FIELD_1[0], FIELD_1[2], FIELD_1[1], FIELD_1[3]]))

    def test_degenerate(self):
        self.assertFalse(same_area([], []))
        self.assertFalse(same_area(FIELD_1[:2], FIELD_1[:2]))


class WorkspaceTest(unittest.TestCase):
    def test_worlds_with_their_origins(self):
        self.assertEqual([w.name for w in WORKSPACE.worlds], ["field_1", "field_1_copy", "field_2"])
        self.assertEqual(WORKSPACE.world("field_1").origin, (49.36275, 14.260778))
        self.assertEqual(WORKSPACE.world("field_1").safety_area, tuple(FIELD_1))
        self.assertIsNone(WORKSPACE.world("nope"))

    def test_world_without_origin_is_centered_on_its_area(self):
        lat, lon = WORKSPACE.world("field_2").origin
        self.assertAlmostEqual(lat, (49.361694 + 49.363417) / 2)
        self.assertAlmostEqual(lon, (14.260750 + 14.263111) / 2)

    def test_all_worlds_with_the_fleet_area_are_active(self):
        border = border_latlon(border_json(FIELD_1[1:] + FIELD_1[:1]))
        self.assertEqual(WORKSPACE.active_worlds(border), ["field_1", "field_1_copy"])
        self.assertEqual(WORKSPACE.active_worlds(border_latlon(border_json(FIELD_2))), ["field_2"])

    def test_no_active_world(self):
        self.assertEqual(WORKSPACE.active_worlds(None), [])
        self.assertEqual(WORKSPACE.active_worlds([]), [])
        self.assertEqual(WORKSPACE.active_worlds([(50.0, 14.0), (50.1, 14.0), (50.1, 14.1)]), [])

    def test_border_in_metres_is_not_compared(self):
        self.assertIsNone(border_latlon(border_json(FIELD_1, frame_id=0)))
        self.assertIsNone(border_latlon(border_json(FIELD_1, frame_id=None)))


if __name__ == "__main__":
    unittest.main()
