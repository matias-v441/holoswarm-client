import dearpygui.dearpygui as dpg
import asyncio
from holoswarm_client.gui.map.window import MapGridWindow
from holoswarm_client.data.mission import Mission
from holoswarm_client.data.session import Session
from holoswarm_client.data.monitoring import Monitoring
from holoswarm_client.gui.map.map import Map
from holoswarm_client.gui.explorer.window import ExplorerWindow 
from holoswarm_client.gui.mission.window import MissionWindow
from holoswarm_client.data.execution import ExecutionStore
from holoswarm_client.data.queue_draft import QueueDraft
from holoswarm_client.services.queues import QueueService

from holoswarm_client.gui.listeners.telemetry import TelemetryListener
from holoswarm_client.gui.listeners.feedback import FeedbackListener
from holoswarm_client.iroc.client import IROCClient
from holoswarm_client.gui.uav.window import UAVWindow

import argparse
import json
import threading
from pathlib import Path


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

    parser.add_argument(
        "--config_dir",
        type=Path,
        default=Path("config"),
        help="Path to the config directory",
    )

    parser.add_argument(
        "--location",
        default="",
        help="Location name",
    )

    parser.add_argument(
        "--mission_path",
        default="",
        help="Mission json path",
    )

    args = parser.parse_args()

    api_loop = asyncio.new_event_loop()

    def asyncio_thread_main():
        asyncio.set_event_loop(api_loop)
        api_loop.run_forever()

    threading.Thread(target=asyncio_thread_main, daemon=True).start()

    dpg.create_context()

    data_dir = Path(".holoswarm_client")
    data_dir.mkdir(parents=True, exist_ok=True)

    default_layout_file = str(args.config_dir / "default_layout.ini")
    user_layout_file = str(data_dir / "layout.ini")

    dpg.configure_app(
        docking=True,
        docking_space=True,
        docking_shift_only=False,
        init_file=user_layout_file if Path(user_layout_file).exists() else default_layout_file,
    )

    mission = Mission(*args.robots)
    session = Session()
    monitoring = Monitoring()


    config = json.loads((args.config_dir / "settings.json").read_bytes())
    location = args.location
    if not location:
        location = config["current_location"]
    origin_lat, origin_lon = config["locations"][location]

    client = IROCClient(server=f"{args.host}:{args.port}")

    # Mission queues: kept by the bridge and mirrored here; the next queue is put together in the draft.
    executions = ExecutionStore()
    queue_draft = QueueDraft()
    queue_service = QueueService(client, api_loop, executions, queue_draft)

    map = Map(
        mission=mission,
        session=session,
        monitoring=monitoring,
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        location_name=location,
        cache_dir=str(data_dir)
    )
    map_window = MapGridWindow(map, client, api_loop)
    map_window.add()

    explorer = ExplorerWindow(mission, session, queue_draft)
    explorer.add()

    mission_window = MissionWindow(queue_service, executions, queue_draft)
    mission_window.add()

    robots: list[UAVWindow] = []
    for robot in args.robots:
        uav = UAVWindow(robot, monitoring, client, api_loop)
        uav.add()
        robots.append(uav)

    telemetry = TelemetryListener(monitoring, client, api_loop)
    telemetry.start()

    # Per-robot feedback of the legacy single-mission path; queue state comes from the queue service.
    feedback = FeedbackListener(monitoring, client, api_loop)
    feedback.start()

    queue_service.start()

    if args.mission_path:
        mission.from_json(json.loads(Path(args.mission_path).read_bytes()))

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
            queue_service.process_events()
            mission_window.process_events()
            dpg.render_dearpygui_frame()
    finally:
        queue_service.stop()
        telemetry.stop()
        feedback.stop()
        dpg.save_init_file(user_layout_file)
        dpg.destroy_context()
