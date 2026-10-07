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
from holoswarm_client.data.queue_view import QueueView
from holoswarm_client.iroc.mission_codec import describe
from holoswarm_client.services.queues import QueueService

from holoswarm_client.gui.listeners.telemetry import TelemetryListener
from holoswarm_client.gui.listeners.feedback import FeedbackListener
from holoswarm_client.iroc.client import IROCClient
from holoswarm_client.gui.robots.window import RobotsWindow

import argparse
import json
import threading
from pathlib import Path

# Distributed with the repository (layouts/ next to src/); the client is installed editable (pip install -e).
DEFAULT_LAYOUT = Path(__file__).resolve().parents[2] / "layouts" / "default.ini"


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
        help="Robots to list before their telemetry arrives (optional: robots are discovered from the telemetry).",
    )

    parser.add_argument(
        "--workspace",
        default="temesvar",
        help="Workspace on the bridge: its worlds and map are shown and new queues are stored in it",
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

    user_layout_file = str(data_dir / "layout.ini")  # saved on exit; overrides the default once it exists

    dpg.configure_app(
        docking=True,
        docking_space=True,
        docking_shift_only=False,
        init_file=user_layout_file if Path(user_layout_file).exists() else str(DEFAULT_LAYOUT),
    )

    mission = Mission(*(args.robots or ()))  # more robots join as their telemetry arrives
    session = Session()
    monitoring = Monitoring()

    client = IROCClient(server=f"{args.host}:{args.port}")

    # Mission queues: kept by the bridge and mirrored here; the next queue is put together in the draft.
    executions = ExecutionStore()
    queue_draft = QueueDraft()
    queue_service = QueueService(client, api_loop, executions, queue_draft, workspace=args.workspace)

    # The origin is the selected world's, once the workspace is loaded from the bridge.
    map = Map(
        mission=mission,
        session=session,
        monitoring=monitoring,
    )
    map_window = MapGridWindow(map, client, api_loop, workspace=args.workspace)
    map_window.add()

    # The map shows every mission of the selected queue; edits on it re-encode their mission right away.
    queue_view = QueueView(mission, session, queue_draft, on_focus=map_window.focus_tasks)

    explorer = ExplorerWindow(mission, session, queue_draft)
    explorer.add()

    mission_window = MissionWindow(queue_service, executions, queue_draft, session, view=queue_view)
    mission_window.add()

    # Robots are discovered from the telemetry stream; --robots only lists them before that.
    robots = RobotsWindow(monitoring, client, api_loop, mission, expected=args.robots or ())
    robots.add()

    telemetry = TelemetryListener(monitoring, client, api_loop)
    telemetry.start()

    # Per-robot feedback of the legacy single-mission path; queue state comes from the queue service.
    feedback = FeedbackListener(monitoring, client, api_loop)
    feedback.start()

    queue_service.start()

    if args.mission_path:
        # A mission of the new queue.
        loaded = json.loads(Path(args.mission_path).read_bytes())
        payload = {"type": loaded["type"], "details": loaded["details"]}
        queue_draft.add(payload, describe(payload), name=Path(args.mission_path).stem)

    dpg.create_viewport(title="holoswarm client", width=1000, height=700)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    try:
        while dpg.is_dearpygui_running():
            monitoring.notify()
            map_window.process_events()
            robots.process_events()
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
