import dearpygui.dearpygui as dpg
import asyncio
from gui.map.window import MapGridWindow
from data.mission import Mission
from data.session import Session
from data.monitoring import Monitoring
from gui.map.model import Map
from gui.explorer.window import ExplorerWindow 
from gui.mission.window import MissionWindow

from listeners.telemetry import TelemetryListener
from listeners.feedback import FeedbackListener
from iroc.client import IROCClient

from gui.uav.window import UAVWindow
import threading

LAYOUT_FILE = "user_layout.ini"


def run():
    api_loop = asyncio.new_event_loop()

    def asyncio_thread_main():
        asyncio.set_event_loop(api_loop)
        api_loop.run_forever()

    threading.Thread(target=asyncio_thread_main, daemon=True).start()

    dpg.create_context()

    dpg.configure_app(
        docking=True,
        docking_space=True,
        docking_shift_only=False,
        init_file=LAYOUT_FILE
    )

    mission = Mission("uav11","uav12")
    session = Session()
    monitoring = Monitoring()

    map = Map(mission=mission, session=session)
    map_window = MapGridWindow(map, api_loop)
    map_window.add()

    client = IROCClient(server="buninmat_pc.sh.cvut.cz:8080")

    explorer = ExplorerWindow(mission, session, client, api_loop)
    explorer.add()

    mission = MissionWindow(monitoring, client, api_loop)
    mission.add()

    uav11 = UAVWindow("uav11", monitoring, client, api_loop)
    uav11.add()

    uav12 = UAVWindow("uav12", monitoring, client, api_loop)
    uav12.add()

    telemetry = TelemetryListener(monitoring, client, api_loop)
    telemetry.start()

    feedback = FeedbackListener(monitoring, client, api_loop)
    feedback.start()

    dpg.create_viewport(title="holoswarm client", width=1000, height=700)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    try:
        while dpg.is_dearpygui_running():
            monitoring.notify()
            map_window.process_events()
            uav11.process_events()
            uav12.process_events()
            explorer.process_events()
            mission.process_events()
            dpg.render_dearpygui_frame()
    finally:
        telemetry.stop()
        feedback.stop()
        dpg.save_init_file(LAYOUT_FILE)
        dpg.destroy_context()

if __name__ == "__main__":
    run()
