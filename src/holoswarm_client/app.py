import dearpygui.dearpygui as dpg
import asyncio
from holoswarm_client.gui.map.window import MapGridWindow
from holoswarm_client.data.mission import Mission
from holoswarm_client.data.session import Session
from holoswarm_client.data.monitoring import Monitoring
from holoswarm_client.gui.map.model import Map
from holoswarm_client.gui.explorer.window import ExplorerWindow 
from holoswarm_client.gui.mission.window import MissionWindow

from holoswarm_client.gui.listeners.telemetry import TelemetryListener
from holoswarm_client.gui.listeners.feedback import FeedbackListener
from holoswarm_client.iroc.client import IROCClient
from holoswarm_client.gui.uav.window import UAVWindow

import argparse
import threading

LAYOUT_FILE = "user_layout.ini"


def main():
    parser = argparse.ArgumentParser(
        prog="holoswarm_client",
        description="Start the Holoswarm client app.",
    )

    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Server host to connect to.",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Server port to connect to.",
    )

    parser.add_argument(
        "--robots",
        nargs="+",
        help="One or more robot names/IDs.",
    )

    args = parser.parse_args()

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

    mission = Mission(*args.robots)
    session = Session()
    monitoring = Monitoring()

    map = Map(mission=mission, session=session)
    map_window = MapGridWindow(map, api_loop)
    map_window.add()

    client = IROCClient(server=f"{args.host}:{args.port}")

    explorer = ExplorerWindow(mission, session, client, api_loop)
    explorer.add()

    mission = MissionWindow(monitoring, client, api_loop)
    mission.add()

    robots: list[UAVWindow] = []
    for robot in args.robots:
        uav = UAVWindow(robot, monitoring, client, api_loop)
        uav.add()
        robots.append(uav)

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
            for uav in robots:
                uav.process_events()
                uav.process_events()
            explorer.process_events()
            mission.process_events()
            dpg.render_dearpygui_frame()
    finally:
        telemetry.stop()
        feedback.stop()
        dpg.save_init_file(LAYOUT_FILE)
        dpg.destroy_context()

