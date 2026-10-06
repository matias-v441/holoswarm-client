import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import quote, urlparse
import httpx
import websockets


DEFAULT_SERVER = "localhost:8080"
MISSION_STATES = ("start", "pause", "stop")
COMMANDS = ("takeoff", "hover", "land", "home", "land_home")
QUEUE_COMMANDS = ("start", "cancel", "pause", "resume")

# The fleet manager gives up on a queue request after 30 s; wait longer so that a slow but
# successful request is not reported as an unknown outcome.
QUEUE_TIMEOUT = 40.0

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
  GET    /terrain/height?lat=deg&lon=deg  terrain height (amsl, dataset datum) and UTM coordinates of a point
  GET    /workspaces
  GET    /workspaces/{name}               worlds (safety areas, origins), map bounds, stored queues
  GET    /workspaces/{name}/map?max_px=N  orthophoto of the workspace as JPEG (X-Map-Bounds: west,south,east,north)
  GET    /schedulers
  GET    /queues?workspace=name
  GET    /queues/{queue_id}
  POST   /queues                          {"scheduler": string, "missions": [{"type": string, "details": object, "id": string?, "name": string?}],
                                           "queue_id": string?, "workspace": string?, "name": string?, "params": object?, "world_id": string?}
  DELETE /queues/{queue_id}
  POST   /queues/{queue_id}/submit
  POST   /queues/{queue_id}/{start|cancel|pause|resume}

WebSocket:
  /telemetry
  /mission/feedback
  /queues/events
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


class ApiError(Exception):
    """The server answered and refused the request."""

    def __init__(self, status: int, message: str, body: Any = None) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message
        self.body = body


class ApiUnavailable(ApiError):
    """The bridge is up but the fleet manager did not answer (503). Nothing was applied."""


def _path(queue_id: str) -> str:
    return f"/queues/{quote(queue_id, safe='')}"


class TransportError(Exception):
    """No answer (timeout, disconnect). The request may or may not have been applied."""


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

    # | ----------------------- mission queues ----------------------- |

    async def _request_json(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        timeout: float = QUEUE_TIMEOUT,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout) as client:
                response = await client.request(method, endpoint, json=payload, params=params)
        except httpx.TransportError as exc:
            raise TransportError(f"{method} {endpoint}: {type(exc).__name__}: {exc}") from exc

        try:
            body = response.json()
        except json.JSONDecodeError:
            body = None

        if response.is_error:
            message = body.get("message") if isinstance(body, dict) else None
            message = message or response.text or response.reason_phrase
            error = ApiUnavailable if response.status_code == 503 else ApiError
            raise error(response.status_code, message, body)

        if not isinstance(body, dict):
            raise ApiError(response.status_code, f"Unexpected response to {method} {endpoint}: {response.text[:200]}")
        return body

    async def schedulers(self) -> list[str]:
        body = await self._request_json("GET", "/schedulers")
        schedulers = body.get("schedulers")
        if not isinstance(schedulers, list):
            raise ApiError(200, "Malformed /schedulers response", body)
        return [str(name) for name in schedulers]

    async def queues(self) -> dict[str, Any]:
        body = await self._request_json("GET", "/queues")
        if not isinstance(body.get("queues"), list) or "session_id" not in body:
            raise ApiError(200, "Malformed /queues response", body)
        return body

    async def queue(self, queue_id: str) -> dict[str, Any]:
        return self._queue_of(await self._request_json("GET", _path(queue_id)))

    async def create_queue(
        self,
        scheduler: str,
        missions: list[dict[str, Any]],
        queue_id: str | None = None,
        name: str | None = None,
        params: dict[str, Any] | None = None,
        world_id: str | None = None,
        workspace: str | None = None,
    ) -> dict[str, Any]:
        """Store a queue with all of its missions on the bridge (in the default workspace unless given). Returns the queue."""
        payload = self._queue_body(scheduler, missions, name, params, world_id, workspace)
        if queue_id:
            payload["queue_id"] = queue_id
        return self._queue_of(await self._request_json("POST", "/queues", payload))

    async def replace_queue(
        self,
        queue_id: str,
        scheduler: str,
        missions: list[dict[str, Any]],
        name: str | None = None,
        params: dict[str, Any] | None = None,
        world_id: str | None = None,
    ) -> dict[str, Any]:
        """Replace a queue that is not submitted as a whole (same id, CREATED again). Returns the queue."""
        payload = self._queue_body(scheduler, missions, name, params, world_id)
        return self._queue_of(await self._request_json("PUT", _path(queue_id), payload))

    async def delete_queue(self, queue_id: str) -> None:
        await self._request_json("DELETE", _path(queue_id))

    async def submit_queue(self, queue_id: str) -> dict[str, Any]:
        """Hand the queue to the fleet manager, which stages its first step. Returns the queue."""
        return self._queue_of(await self._request_json("POST", f"{_path(queue_id)}/submit"))

    async def control_queue(self, queue_id: str, command: str) -> dict[str, Any]:
        _check_choice(command, QUEUE_COMMANDS, "queue command")
        return await self._request_json("POST", f"{_path(queue_id)}/{command}")

    async def queue_events(self) -> AsyncIterator[dict[str, Any]]:
        """Queue changes: {"type": "queue", "queue": {...}} or {"type": "queue_removed", "queue_id"}, with session_id and seq."""
        async for message in self.websocket_messages("/queues/events"):
            event = json.loads(message)
            if isinstance(event, dict) and event.get("type") in ("queue", "queue_removed"):
                yield event

    # | ----------------------- terrain ----------------------- |

    async def terrain_height(self, lat: float, lon: float) -> dict[str, Any]:
        """{"lat", "lon", "valid", "amsl": float | None, "utm": {"zone", "x", "y"}} of a WGS84 point."""
        return await self._request_json("GET", "/terrain/height", params={"lat": repr(lat), "lon": repr(lon)},
                                        timeout=self.timeout)

    # | ----------------------- workspaces ----------------------- |

    async def workspaces(self) -> dict[str, Any]:
        """{"default": name, "workspaces": [{"name", "description", "worlds": [names]}]}"""
        return await self._request_json("GET", "/workspaces")

    async def workspace(self, name: str) -> dict[str, Any]:
        """The workspace: worlds (origin, min/max z, safety area, bounds), map bounds and url, queues."""
        body = await self._request_json("GET", f"/workspaces/{quote(name, safe='')}")
        if not isinstance(body.get("workspace"), dict):
            raise ApiError(200, "Malformed workspace response", body)
        return body["workspace"]

    async def workspace_map(self, name: str, max_px: int = 4096) -> tuple[bytes, tuple[float, float, float, float]]:
        """The workspace's orthophoto (JPEG) and its extent (west, south, east, north) in degrees."""
        endpoint = f"/workspaces/{quote(name, safe='')}/map"
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=QUEUE_TIMEOUT) as client:
                response = await client.get(endpoint, params={"max_px": max_px})
        except httpx.TransportError as exc:
            raise TransportError(f"GET {endpoint}: {type(exc).__name__}: {exc}") from exc
        if response.is_error:
            try:
                message = response.json().get("message")
            except (json.JSONDecodeError, AttributeError):
                message = None
            error = ApiUnavailable if response.status_code == 503 else ApiError
            raise error(response.status_code, message or response.text or response.reason_phrase)
        try:
            west, south, east, north = (float(v) for v in response.headers["X-Map-Bounds"].split(","))
        except (KeyError, ValueError) as exc:
            raise ApiError(response.status_code, f"Map without valid X-Map-Bounds header: {exc}") from exc
        return response.content, (west, south, east, north)

    @staticmethod
    def _queue_body(scheduler: str, missions: list[dict[str, Any]], name: str | None, params: dict[str, Any] | None,
                    world_id: str | None, workspace: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"scheduler": scheduler, "missions": missions}
        if workspace:
            payload["workspace"] = workspace
        if name:
            payload["name"] = name
        if params:
            payload["params"] = params
        if world_id:
            payload["world_id"] = world_id
        return payload

    @staticmethod
    def _queue_of(body: dict[str, Any]) -> dict[str, Any]:
        queue = body.get("queue")
        if not isinstance(queue, dict):
            raise ApiError(200, "Malformed queue response", body)
        return queue

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

    subparsers.add_parser("queues", help="Print all mission queues")

    queue_create_parser = subparsers.add_parser("queue-create", help="Create a queue from mission JSON files (in execution order)")
    queue_create_parser.add_argument("json_paths", nargs="+")
    queue_create_parser.add_argument("--scheduler", default="batch")
    queue_create_parser.add_argument("--queue-id")
    queue_create_parser.add_argument("--name")
    queue_create_parser.add_argument("--workspace")

    queue_submit_parser = subparsers.add_parser("queue-submit", help="Hand a queue to the fleet manager (stages its first step)")
    queue_submit_parser.add_argument("queue_id")

    queue_delete_parser = subparsers.add_parser("queue-delete", help="Remove a queue")
    queue_delete_parser.add_argument("queue_id")

    queue_control_parser = subparsers.add_parser("queue", help="Show or control a mission queue")
    queue_control_parser.add_argument("queue_id")
    queue_control_parser.add_argument("queue_command", nargs="?", choices=QUEUE_COMMANDS)

    terrain_parser = subparsers.add_parser("terrain-height", help="Terrain height and UTM coordinates of a point")
    terrain_parser.add_argument("lat", type=float)
    terrain_parser.add_argument("lon", type=float)

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
    elif args.command == "queues":
        print(json.dumps(await client.queues(), indent=2))
    elif args.command == "queue-create":
        missions = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.json_paths]
        print(json.dumps(await client.create_queue(args.scheduler, missions, queue_id=args.queue_id, name=args.name,
                                                   workspace=args.workspace), indent=2))
    elif args.command == "queue-submit":
        print(json.dumps(await client.submit_queue(args.queue_id), indent=2))
    elif args.command == "queue-delete":
        await client.delete_queue(args.queue_id)
        print(f"Removed {args.queue_id}")
    elif args.command == "queue":
        if args.queue_command:
            print(json.dumps(await client.control_queue(args.queue_id, args.queue_command), indent=2))
        else:
            print(json.dumps(await client.queue(args.queue_id), indent=2))
    elif args.command == "terrain-height":
        print(json.dumps(await client.terrain_height(args.lat, args.lon), indent=2))
    elif args.command == "ws":
        async for message in client.websocket_messages(args.endpoint):
            print(message)
    elif args.command == "rc-message":
        print(await client.rc_message(args.message))
    elif args.command == "rc-move":
        print(await client.rc_move(args.robot_name, args.x, args.y, args.z, args.heading))


if __name__ == "__main__":
    asyncio.run(main())
