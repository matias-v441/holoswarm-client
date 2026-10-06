import unittest
from dataclasses import replace

import dearpygui.dearpygui as dpg

from holoswarm_client.data.mission import Coverage, Mission
from holoswarm_client.data.session import Session
from holoswarm_client.gui.map.map import Map
from holoswarm_client.gui.map.views.coverage import CoveragePrimitive


POINTS = ((49.36, 14.26), (49.361, 14.26), (49.361, 14.261))


class CoverageViewTest(unittest.TestCase):
    def setUp(self):
        dpg.create_context()
        self.addCleanup(dpg.destroy_context)
        with dpg.window():
            self.drawlist = dpg.add_drawlist(width=400, height=400)
        self.mission = Mission()
        self.session = Session()
        self.map = Map(self.mission, self.session, monitoring=None)

    def area(self, points):
        area = Coverage(points=points, time_interval=(0., 1.), height_id=0, height=5.)
        self.mission.push_task(area)
        view = CoveragePrimitive(self.map, self.drawlist, area.uuid)
        self.addCleanup(view.dispose)
        view.draw()
        return area, view

    def outline(self, view):
        outlines = [item for item in view.items
                    if dpg.get_item_info(item)["type"] == "mvAppItemType::mvDrawPolyline"]
        self.assertEqual(len(outlines), 1)
        return dpg.get_item_configuration(outlines[0])

    def test_first_deselection_closes_area_and_reselection_keeps_it_closed(self):
        area, view = self.area(POINTS[:1])
        self.session.select_task(area.uuid)
        self.mission.push_task(replace(area, points=POINTS))
        self.assertFalse(self.outline(view)["closed"])

        self.session.select_task(None)
        self.assertTrue(self.outline(view)["closed"])
        self.session.select_task(area.uuid)
        self.assertTrue(self.outline(view)["closed"])
        self.assertEqual(self.mission.tasks[area.uuid].points, POINTS)

    def test_selecting_another_task_closes_area(self):
        area, view = self.area(POINTS[:1])
        self.session.select_task(area.uuid)
        self.mission.push_task(replace(area, points=POINTS))
        self.session.select_task("another-task")
        self.assertTrue(self.outline(view)["closed"])

    def test_loaded_area_is_closed_even_when_selected(self):
        area, view = self.area(POINTS)
        self.assertTrue(self.outline(view)["closed"])
        self.session.select_task(area.uuid)
        self.assertTrue(self.outline(view)["closed"])
