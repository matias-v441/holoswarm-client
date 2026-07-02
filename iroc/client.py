import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import httpx
import websockets


DEFAULT_SERVER = "localhost:8080"
MISSION_STATES = ("start", "pause", "stop")
COMMANDS = ("takeoff", "hover", "land", "home", "land_home")

API = """
HTTP:
  GET  /robots
  GET  /safety-area/world-origin
  POST /safety-area/world-origin          {"x": float, "y": float}
  GET  /safety-area/borders
  POST /safety-area/borders               {"height_id": 0|1, "min_z": number, "max_z": number, "points": [{"x": number, "y": number}]}
  GET  /safety-area/obstacles
  POST /safety-area/obstacles             {"obstacles": [{"height_id": 0|1, "min_z": number, "max_z": number, "points": [{"x": number, "y": number}]}]}
  GET  /mission
  POST /mission                           {"type": string, "uuid": string?, "details": object}
  POST /mission/{start|pause|stop}
  POST /robots/{robot_name}/mission/{start|pause|stop}
  POST /robots/{takeoff|hover|land|home|land_home}
  POST /robots/{robot_name}/{takeoff|hover|land|home|land_home}

WebSocket:
  /telemetry
  /mission/feedback
  /rc                                   send {"command": "message", "data": string}
  /rc                                   send {"command": "move", "robot_name": string, "data": {"x": -1..1, "y": -1..1, "z": -1..1, "heading": -1..1}}
"""


def _with_scheme(server: str, scheme: str = "http") -> str:
    parsed = urlparse(server)
    if parsed.scheme:
        return server.rstrip("/")
    return f"{scheme}://{server.rstrip('/')}"


def _check_choice(value: str, choices: tuple[str, ...], label: str) -> None:
    if value not in choices:
        joined = ", ".join(choices)
        raise ValueError(f"Unknown {label} {value!r}. Expected one of: {joined}")


class IROCClient:
    def __init__(self, server: str = DEFAULT_SERVER, timeout: float = 20.0) -> None:
        self.base_url = _with_scheme(server, "http")
        self.ws_base_url = _with_scheme(server, "ws")
        self.timeout = timeout

    async def get(self, endpoint: str) -> httpx.Response:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            response = await client.get(endpoint)
            response.raise_for_status()
            return response

    async def post(self, endpoint: str, payload: dict[str, Any] | None = None) -> httpx.Response:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            response = await client.post(endpoint, json=payload or {})
            response.raise_for_status()
            return response

    async def post_file(self, endpoint: str, json_path: str | Path) -> httpx.Response:
        payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
        return await self.post(endpoint, payload)

    async def robots(self) -> httpx.Response:
        return await self.get("/robots")

    async def get_world_origin(self) -> httpx.Response:
        return await self.get("/safety-area/world-origin")

    async def set_world_origin(self, x: float, y: float) -> httpx.Response:
        return await self.post("/safety-area/world-origin", {"x": x, "y": y})

    async def get_borders(self) -> httpx.Response:
        return await self.get("/safety-area/borders")

    async def set_borders(
        self,
        points: list[dict[str, float]],
        min_z: float,
        max_z: float,
        height_id: int = 0,
    ) -> httpx.Response:
        payload = {
            "height_id": height_id,
            "min_z": min_z,
            "max_z": max_z,
            "points": points,
        }
        return await self.post("/safety-area/borders", payload)

    async def get_obstacles(self) -> httpx.Response:
        return await self.get("/safety-area/obstacles")

    async def set_obstacles(self, obstacles: list[dict[str, Any]]) -> httpx.Response:
        return await self.post("/safety-area/obstacles", {"obstacles": obstacles})

    async def get_mission(self) -> httpx.Response:
        return await self.get("/mission")

    async def upload_mission(self, mission: dict[str, Any]) -> httpx.Response:
        return await self.post("/mission", mission)

    async def upload_mission_file(self, json_path: str | Path) -> httpx.Response:
        return await self.post_file("/mission", json_path)

    async def fleet_mission(self, state: str) -> httpx.Response:
        _check_choice(state, MISSION_STATES, "mission state")
        return await self.post(f"/mission/{state}")

    async def start_mission(self) -> httpx.Response:
        return await self.fleet_mission("start")

    async def pause_mission(self) -> httpx.Response:
        return await self.fleet_mission("pause")

    async def stop_mission(self) -> httpx.Response:
        return await self.fleet_mission("stop")

    async def robot_mission(self, robot_name: str, state: str) -> httpx.Response:
        _check_choice(state, MISSION_STATES, "mission state")
        return await self.post(f"/robots/{robot_name}/mission/{state}")

    async def command(self, command_type: str, robot_name: str | None = None) -> httpx.Response:
        _check_choice(command_type, COMMANDS, "command")
        if robot_name is None:
            return await self.post(f"/robots/{command_type}")
        return await self.post(f"/robots/{robot_name}/{command_type}")

    async def takeoff(self, robot_name: str | None = None) -> httpx.Response:
        return await self.command("takeoff", robot_name)

    async def hover(self, robot_name: str | None = None) -> httpx.Response:
        return await self.command("hover", robot_name)

    async def land(self, robot_name: str | None = None) -> httpx.Response:
        return await self.command("land", robot_name)

    async def home(self, robot_name: str | None = None) -> httpx.Response:
        return await self.command("home", robot_name)

    async def websocket_messages(self, endpoint: str) -> AsyncIterator[str]:
        url = f"{self.ws_base_url}/{endpoint.lstrip('/')}"
        async with websockets.connect(url) as websocket:
            async for message in websocket:
                yield message

    async def telemetry(self) -> AsyncIterator[str]:
        async for message in self.websocket_messages("/telemetry"):
            yield message

    async def mission_feedback(self) -> AsyncIterator[str]:
        async for message in self.websocket_messages("/mission/feedback"):
            yield message

    async def rc_send(self, payload: dict[str, Any]) -> str:
        async with websockets.connect(f"{self.ws_base_url}/rc") as websocket:
            await websocket.send(json.dumps(payload))
            return await websocket.recv()

    async def rc_message(self, message: str) -> str:
        return await self.rc_send({"command": "message", "data": message})

    async def rc_move(
        self,
        robot_name: str,
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.0,
        heading: float = 0.0,
    ) -> str:
        return await self.rc_send(
            {
                "command": "move",
                "robot_name": robot_name,
                "data": {"x": x, "y": y, "z": z, "heading": heading},
            }
        )


def print_response(response: httpx.Response) -> None:
    print(f"HTTP {response.status_code}")
    try:
        print(json.dumps(response.json(), indent=2))
    except json.JSONDecodeError:
        print(response.text)


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Example client for the IROC bridge server in hhh.cpp")
    parser.add_argument("--server", default=DEFAULT_SERVER, help="Host[:port] or URL")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("api", help="Print the HTTP and websocket API")

    get_parser = subparsers.add_parser("get", help="GET an endpoint")
    get_parser.add_argument("endpoint", nargs="?", default="/robots")

    post_parser = subparsers.add_parser("post", help="POST a JSON file to an endpoint")
    post_parser.add_argument("endpoint", nargs="?", default="/mission")
    post_parser.add_argument("json_path")

    mission_parser = subparsers.add_parser("mission", help="Change fleet mission state")
    mission_parser.add_argument("state", choices=MISSION_STATES)

    robot_mission_parser = subparsers.add_parser("robot-mission", help="Change one robot mission state")
    robot_mission_parser.add_argument("robot_name")
    robot_mission_parser.add_argument("state", choices=MISSION_STATES)

    command_parser = subparsers.add_parser("command", help="Send robot command")
    command_parser.add_argument("command_type", choices=COMMANDS)
    command_parser.add_argument("robot_name", nargs="?")

    ws_parser = subparsers.add_parser("ws", help="Print messages from a websocket endpoint")
    ws_parser.add_argument("endpoint")

    rc_message_parser = subparsers.add_parser("rc-message", help="Send a test message over /rc")
    rc_message_parser.add_argument("message")

    rc_move_parser = subparsers.add_parser("rc-move", help="Send a normalized movement command over /rc")
    rc_move_parser.add_argument("robot_name")
    rc_move_parser.add_argument("--x", type=float, default=0.0)
    rc_move_parser.add_argument("--y", type=float, default=0.0)
    rc_move_parser.add_argument("--z", type=float, default=0.0)
    rc_move_parser.add_argument("--heading", type=float, default=0.0)

    args = parser.parse_args()
    client = IROCClient(args.server)

    if args.command == "api":
        print(API.strip())
    elif args.command == "get":
        print_response(await client.get(args.endpoint))
    elif args.command == "post":
        print_response(await client.post_file(args.endpoint, args.json_path))
    elif args.command == "mission":
        print_response(await client.fleet_mission(args.state))
    elif args.command == "robot-mission":
        print_response(await client.robot_mission(args.robot_name, args.state))
    elif args.command == "command":
        print_response(await client.command(args.command_type, args.robot_name))
    elif args.command == "ws":
        async for message in client.websocket_messages(args.endpoint):
            print(message)
    elif args.command == "rc-message":
        print(await client.rc_message(args.message))
    elif args.command == "rc-move":
        print(await client.rc_move(args.robot_name, args.x, args.y, args.z, args.heading))


if __name__ == "__main__":
    asyncio.run(main())
