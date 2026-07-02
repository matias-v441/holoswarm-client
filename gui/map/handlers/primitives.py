import dearpygui.dearpygui as dpg
from gui.map.model import Map
from data.mission import *
from data.session import *
from dataclasses import replace

from collections.abc import Callable

Point = tuple[float,float]

def ctrl_pressed() -> bool:
    return dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)

class Primitives:

    def __init__(self, map: Map, drawlist_tag: str):
        self.map = map
        self.drawlist_tag = drawlist_tag
        self.active_path_uuid:str = None
        self.mission = map.mission
        self.session = map.session
        self.session.subscribe(self.session_cb)

    def session_cb(self, session):
        self.active_path_uuid = None
        selection: Selection = session.selection
        if not selection:
            return

        tasks = self.mission.tasks
        for item_uuid in selection.items:
            if isinstance(tasks.get(item_uuid), Waypoints):
                self.active_path_uuid = item_uuid
                return

    def on_down(self, mouse) -> bool:

        clicked_uuid = self._find_clicked_uuid(mouse)

        if not ctrl_pressed():
            if not clicked_uuid:
                selection = self.session.selection
                if selection:
                    self.session.pop(selection.uuid)
            return False

        if clicked_uuid:
            selection = self.session.selection
            if not selection:
                selection = Selection()
            selection = replace(selection, items=(clicked_uuid,))
            self.session.push(selection)
            return True

        origin = self.map.origin_canvas(self.map.width, self.map.height)
        world_at_mouse = self.map.canvas_to_world(mouse, origin)

        point = PointLocal((*world_at_mouse,5.),heading=0.)
        if not self.active_path_uuid:
            wp = Waypoints[PointLocal](points=(point,),time_interval=(0.,1.))
            self.mission.push_task(wp)
            selection = self.session.selection
            if not selection:
                selection = Selection()
            selection = replace(selection, items=(wp.uuid,))
            self.session.push(selection)
        else:
            wp:Waypoints[PointLocal] = self.mission.tasks[self.active_path_uuid]
            wp = replace(wp, points=(*wp.points,point))
            self.mission.push_task(wp)
        return True

    def _find_clicked_uuid(self, mouse, radius=16.) -> str|None:
        for wp in self.mission.waypoints.values():
            for point in wp.points:
                point: PointLocal | PointGlobal
                if wp.frame == FrameID.LOCAL:
                    center = self.map.world_to_canvas(point.position[:2])
                elif wp.frame == FrameID.GLOBAL:
                    center = self.map.world_to_canvas(self.map.latlon_to_world(point.lat, point.lon))
                dx = center[0] - mouse[0]
                dy = center[1] - mouse[1]
                if dx**2 + dy**2 <= radius**2:
                    return wp.uuid
        return None
                
