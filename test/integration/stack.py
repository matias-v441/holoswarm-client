"""The compose stack (ROOT/compose) the integration tests run against.

HOLOSWARM_TEST_STACK selects the environment; the tests start it with compose/up.sh and stop it with
compose/down.sh. They never touch a stack that is already running: if any ground / sim / uav*
container is up (it may be a live experiment), the tests are skipped instead.

  ground  ground-only stack + fake mission handlers for uav1, uav2 (fast; default level)
  sim     one simulated drone (uav1) that stays on the ground: the real mission handler stages goals
  flight  one simulated drone (uav1) that takes off: queues really fly

HOLOSWARM_TEST_KEEP=1 leaves the stack running afterwards (e.g. to read its logs).
HOLOSWARM_COMPOSE_DIR overrides the location of compose/ (default: ../compose next to holoswarm-client).
"""

import asyncio
import json
import os
import re
import subprocess
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

import httpx
import websockets

COMPOSE_DIR = Path(os.environ.get("HOLOSWARM_COMPOSE_DIR", Path(__file__).resolve().parents[3] / "compose"))
BRIDGE = "127.0.0.1:8080"
FLEET_MANAGER_CONTAINER = "ground-fleet_manager-1"
BRIDGE_CONTAINER = "ground-iroc_bridge-1"
# The bridge stores queues in a database of its own during tests, never in the operator's
# (compose/testing/bridge_test.yaml; ROOT/.assets is /var/lib/holoswarm/assets in the containers).
BRIDGE_TEST_CONFIG = "./compose/testing/bridge_test.yaml"
TEST_DB = COMPOSE_DIR.parent / ".assets" / "test.sqlite"
STACK_CONTAINER = re.compile(r"^(ground|sim|uav\d+)-")


@dataclass(frozen=True)
class Environment:
    name: str
    mode: str
    env: dict[str, str]
    robot: str            # the robot the tests plan missions for
    fake_robots: bool     # fake mission handlers (refuse uploads of tasks with "refuse-upload" in their id)
    can_run: bool         # started queues execute (fake robots, or a flying drone)
    flying: bool
    startup_timeout: float
    mission_timeout: float  # [s] for one short route to finish


ENVIRONMENTS = {
    "ground": Environment("ground", "ground-only", {"FAKE_ROBOTS": "uav1 uav2"}, "uav1", True, True, False, 60, 30),
    "sim": Environment("sim", "simulation", {"SIM_UAVS": "uav1", "SIM_TAKEOFF": "0"}, "uav1", False, False, False, 180, 0),
    "flight": Environment("flight", "simulation", {"SIM_UAVS": "uav1"}, "uav1", False, True, True, 400, 180),
}

# Local frame of compose/simulation (world_bechovice): uav1 spawns at x=-20, the safety area reaches
# ~130 m west and ~15 m east of the origin, z >= 1 m.
SPAWN_X = {"uav1": -20.0, "uav2": -10.0}


def route(robot: str, index: int, mission_id: str | None = None) -> dict:
    """A short waypoint mission near the robot's spawn point."""
    x = SPAWN_X.get(robot, -20.0)
    return {
        "id": mission_id or f"route-{index}",
        "name": f"Route {index}",
        "type": "WaypointPlanner",
        "details": {"robots": [{
            "name": robot, "frame_id": 0, "height_id": 0, "terminal_action": 0,
            "points": [{"x": x, "y": 3.0 + index, "z": 4.0, "heading": 0.0},
                       {"x": x - 4.0, "y": 3.0 + index, "z": 4.0, "heading": 0.0}],
        }]},
    }


def running_containers() -> list[str]:
    names = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, check=True).stdout.split()
    return sorted(n for n in names if STACK_CONTAINER.match(n))


def _http_ok(path: str) -> bool:
    try:
        return httpx.get(f"http://{BRIDGE}{path}", timeout=3).status_code in (200, 202)
    except httpx.HTTPError:
        return False


def wait_until(condition, timeout: float, what: str, period: float = 0.5) -> None:
    deadline = time.time() + timeout
    while not condition():
        if time.time() > deadline:
            raise TimeoutError(f"{what} not within {timeout:.0f} s")
        time.sleep(period)


def wait_fleet_manager(timeout: float = 60.0) -> None:
    wait_until(lambda: _http_ok("/schedulers"), timeout, "fleet manager answering through the bridge")


def wait_robots(robots: list[str], timeout: float) -> None:
    def present() -> bool:
        try:
            response = httpx.get(f"http://{BRIDGE}/robots", timeout=3)
            return set(robots) <= {r.get("name") for r in response.json()}
        except (httpx.HTTPError, ValueError, AttributeError):
            return False
    wait_until(present, timeout, f"diagnostics of {', '.join(robots)}", period=1.0)


def wait_hovering(robots: list[str], timeout: float) -> None:
    async def watch() -> None:
        states: dict[str, str] = {}
        async with websockets.connect(f"ws://{BRIDGE}/telemetry") as ws:
            while not all(states.get(r) == "HOVER" for r in robots):
                message = json.loads(await ws.recv())
                if message.get("type") == "UavInfo":
                    states[message.get("robot_name")] = message.get("flight_state")
    try:
        asyncio.run(asyncio.wait_for(watch(), timeout))
    except asyncio.TimeoutError:
        raise TimeoutError(f"{', '.join(robots)} not hovering within {timeout:.0f} s") from None


def wait_staging_works(robot: str, timeout: float) -> None:
    """A simulated drone's mission handler can only stage goals once its MRS core is up, a little after
    it publishes diagnostics. Stage (and cancel) a throwaway queue until that works."""
    base = f"http://{BRIDGE}"
    deadline = time.time() + timeout
    last = ""
    with httpx.Client(base_url=base, timeout=10) as http:
        while True:
            queue_id = f"probe-{int(time.time() * 1000)}"
            http.post("/queues", json={"queue_id": queue_id, "scheduler": "batch", "missions": [route(robot, 0)]})
            http.post(f"/queues/{queue_id}/submit")
            state = ""
            while time.time() < deadline:
                queue = http.get(f"/queues/{queue_id}").json()["queue"]
                state, last = queue["state"], queue["message"] or last
                if state not in ("SUBMITTING", "UPLOADING"):
                    break
                time.sleep(0.3)
            if state == "READY":
                http.post(f"/queues/{queue_id}/cancel")
                wait_until(lambda: http.get(f"/queues/{queue_id}").json()["queue"]["state"] == "CANCELLED", 30, "probe queue cancelled")
            http.delete(f"/queues/{queue_id}")
            if state == "READY":
                return
            if time.time() > deadline:
                raise TimeoutError(f"{robot} cannot stage a goal within {timeout:.0f} s: {last}")
            time.sleep(2.0)


class Stack:
    """The environment of this test run; started once per test module."""

    def __init__(self) -> None:
        self.env: Environment | None = None
        self.started = False

    def start(self) -> Environment:
        name = os.environ.get("HOLOSWARM_TEST_STACK", "")
        if not name:
            raise unittest.SkipTest("set HOLOSWARM_TEST_STACK=ground|sim|flight to run against the compose stack (see README.md)")
        if name not in ENVIRONMENTS:
            raise ValueError(f"HOLOSWARM_TEST_STACK={name!r}: expected one of {', '.join(ENVIRONMENTS)}")
        if not (COMPOSE_DIR / "up.sh").exists():
            raise unittest.SkipTest(f"no compose setup at {COMPOSE_DIR} (set HOLOSWARM_COMPOSE_DIR)")
        running = running_containers()
        if running:
            raise unittest.SkipTest("a compose stack is already running and is left alone (it may be a live experiment); "
                                    f"stop it with compose/down.sh first: {', '.join(running)}")

        env = ENVIRONMENTS[name]
        self.env = env
        print(f"\n[stack] {' '.join(f'{k}={v!r}' for k, v in env.env.items())} ./up.sh {env.mode}", flush=True)
        self.started = True
        for path in (TEST_DB, TEST_DB.with_name(TEST_DB.name + "-wal"), TEST_DB.with_name(TEST_DB.name + "-shm")):
            path.unlink(missing_ok=True)  # every run starts without stored queues
        result = subprocess.run(["./up.sh", env.mode], cwd=COMPOSE_DIR, env={**os.environ, **env.env, "BRIDGE_CUSTOM_CONFIG": BRIDGE_TEST_CONFIG},
                                capture_output=True, text=True)
        if result.returncode != 0:
            self.stop()
            raise RuntimeError(f"up.sh {env.mode} failed:\n{result.stdout}\n{result.stderr}")

        try:
            begin = time.time()
            wait_until(lambda: _http_ok("/queues"), env.startup_timeout, "bridge")
            wait_fleet_manager(env.startup_timeout)
            wait_robots([env.robot], env.startup_timeout)
            if not env.fake_robots:
                wait_staging_works(env.robot, env.startup_timeout)
            if env.flying:
                wait_hovering([env.robot], env.startup_timeout)
            print(f"[stack] ready after {time.time() - begin:.0f} s", flush=True)
        except TimeoutError:
            self.stop()
            raise
        return env

    def stop(self) -> None:
        if not self.started:
            return
        self.started = False
        if os.environ.get("HOLOSWARM_TEST_KEEP"):
            print("[stack] HOLOSWARM_TEST_KEEP: left running; stop it with compose/down.sh", flush=True)
            return
        print("[stack] ./down.sh", flush=True)
        subprocess.run(["./down.sh"], cwd=COMPOSE_DIR, capture_output=True, text=True)

    def restart_fleet_manager(self) -> None:
        subprocess.run(["docker", "restart", FLEET_MANAGER_CONTAINER], capture_output=True, check=True)
        wait_fleet_manager()

    def restart_bridge(self) -> None:
        """Restart the bridge container: it loads its stored queues again."""
        subprocess.run(["docker", "restart", BRIDGE_CONTAINER], capture_output=True, check=True)
        wait_until(lambda: _http_ok("/queues"), 60, "bridge after restart")
        wait_fleet_manager()

    def stop_fleet_manager(self) -> None:
        subprocess.run(["docker", "stop", FLEET_MANAGER_CONTAINER], capture_output=True, check=True)

    def start_fleet_manager(self) -> None:
        subprocess.run(["docker", "start", FLEET_MANAGER_CONTAINER], capture_output=True, check=True)
        wait_fleet_manager()
        assert self.env is not None
        wait_robots([self.env.robot], 60)


STACK = Stack()
