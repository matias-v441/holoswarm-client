"""Mission queue API of the bridge, driven through IROCClient against the compose stack.

The tests start the stack themselves (compose/up.sh) and stop it afterwards (compose/down.sh);
HOLOSWARM_TEST_STACK picks the environment (see stack.py and README.md in this directory):

  HOLOSWARM_TEST_STACK=ground  ground-only stack with fake mission handlers (fast, default level)
  HOLOSWARM_TEST_STACK=sim     one simulated drone on the ground: the real mission handler stages goals
  HOLOSWARM_TEST_STACK=flight  one simulated drone that takes off: started queues really fly

Without HOLOSWARM_TEST_STACK, or while another compose stack is running, everything is skipped.
"""
import asyncio
import io
import json
import time
import unittest
import uuid
from pathlib import Path

import httpx
import websockets
from PIL import Image

from holoswarm_client.iroc.client import ApiError, ApiUnavailable, IROCClient

from stack import BRIDGE, STACK, Environment, route, wait_sampler

MISSIONS_DIR = Path(__file__).resolve().parents[1] / "json" / "missions"
ENV: Environment | None = None
IN_FLIGHT = ("SUBMITTING", "UPLOADING", "READY", "RUNNING", "CANCELLING")
TERMINAL = ("FINISHED", "CANCELLED", "REJECTED", "INTERRUPTED")


def setUpModule():
    global ENV
    ENV = STACK.start()


def tearDownModule():
    STACK.stop()


def load_mission(name: str) -> dict:
    return json.loads((MISSIONS_DIR / name).read_text(encoding="utf-8"))


PRECISE_MISSION = {
    "type": "CoveragePlanner",
    "id": "precise",
    "name": "Přesná oblast",
    "priority": 3,
    "return_home_policy": 2,
    "details": {
        "robots": ["uav1"],
        "search_area": [
            {"x": 47.39797812345678, "y": 8.545299000000001},
            {"x": -0.1, "y": 1e-7},
            {"x": 50.0, "y": 14.123456789012345},
        ],
        "height_id": 0,
        "height": 5.5,
        "terminal_action": 0,
        "extra": {"flag": True, "nothing": None, "list": [1, 2.25, "three"]},
    },
}


class QueueTestCase(unittest.IsolatedAsyncioTestCase):
    """Creates queues with unique ids and removes them again, cancelling any that is still in flight."""

    async def asyncSetUp(self):
        # IsolatedAsyncioTestCase runs in debug mode, which reports every step over 0.1 s; IROCClient
        # sets up a new HTTP client per request, which alone takes about that long.
        asyncio.get_running_loop().slow_callback_duration = 1.0
        self.client = IROCClient(BRIDGE)
        self.env = ENV
        self.created: list[str] = []

    async def asyncTearDown(self):
        for queue_id in self.created:
            try:
                queue = await self.client.queue(queue_id)
                if queue["state"] in ("UPLOADING", "READY", "RUNNING"):
                    await self.client.control_queue(queue_id, "cancel")
                if queue["state"] in IN_FLIGHT:
                    await self.wait_state(queue_id, *TERMINAL, timeout=max(60.0, self.env.mission_timeout))
                await self.client.delete_queue(queue_id)
            except ApiError:
                pass

    def new_id(self) -> str:
        queue_id = f"it-{uuid.uuid4().hex[:12]}"
        self.created.append(queue_id)
        return queue_id

    async def create(self, missions=None, **kwargs) -> dict:
        kwargs.setdefault("queue_id", self.new_id())
        return await self.client.create_queue(kwargs.pop("scheduler", "batch"), missions or [load_mission("coverage.json")], **kwargs)

    def routes(self, count: int) -> list[dict]:
        return [route(self.env.robot, i + 1) for i in range(count)]

    async def queue_ids(self) -> list[str]:
        return [q["queue_id"] for q in (await self.client.queues())["queues"]]

    async def assert_refused(self, status: int, coro):
        with self.assertRaises(ApiError) as caught:
            await coro
        self.assertEqual(caught.exception.status, status, caught.exception.message)
        self.assertTrue(caught.exception.message)
        return caught.exception

    async def wait_state(self, queue_id: str, *states: str, timeout: float = 30.0) -> dict:
        """Poll until the queue is in one of the states; the last queue seen is in the failure message."""
        deadline = time.time() + timeout
        while True:
            queue = await self.client.queue(queue_id)
            if queue["state"] in states:
                return queue
            if time.time() > deadline:
                self.fail(f"queue {queue_id} is {queue['state']} after {timeout:.0f} s, expected {states}: "
                          f"{queue['message']} {[(m['id'], m['state'], m['message']) for m in queue['missions']]}")
            await asyncio.sleep(0.3)

    async def submit_when_robots_known(self, queue_id: str, timeout: float = 30.0) -> None:
        """Submit right after a fleet manager (re)start: it refuses to plan until the robots' diagnostics arrived."""
        deadline = time.time() + timeout
        while True:
            try:
                await self.client.submit_queue(queue_id)
                return
            except ApiError as exc:
                not_yet = exc.status == 400 and ("not found in the fleet" in exc.message or "not yet populated" in exc.message)
                if not not_yet or time.time() > deadline:
                    raise
            await asyncio.sleep(0.5)

    async def submit_ready(self, missions: list[dict], **kwargs) -> dict:
        queue = await self.create(missions, **kwargs)
        await self.client.submit_queue(queue["queue_id"])
        return await self.wait_state(queue["queue_id"], "READY", "REJECTED")


class QueueStoreTest(QueueTestCase):
    """Creating, reading and deleting queues: the bridge alone, the fleet manager is not involved."""

    # | ----------------------- create / read ----------------------- |

    async def test_create_echoes_the_queue(self):
        missions = [PRECISE_MISSION, load_mission("coverage.json"), load_mission("two_drones.json"), {"type": "WaypointPlanner", "details": {"robots": []}}]
        queue_id = self.new_id()
        queue = await self.client.create_queue("fleet", missions, queue_id=queue_id, name="Survey", params={"assignment_policy": "nearest"},
                                               world_id="prague")

        self.assertEqual(queue["queue_id"], queue_id)
        self.assertEqual(queue["name"], "Survey")
        self.assertEqual(queue["scheduler"], "fleet")
        self.assertEqual(queue["params"], {"assignment_policy": "nearest"})
        self.assertEqual(queue["world_id"], "prague")
        self.assertEqual(queue["state"], "CREATED")
        self.assertFalse(queue["paused"])
        self.assertEqual(queue["message"], "")

        ids = [m["id"] for m in queue["missions"]]
        self.assertEqual(ids[:3], ["precise", missions[1]["uuid"], missions[2]["uuid"]], "ids kept, 'uuid' accepted, order kept")
        self.assertTrue(ids[3], "a missing id is generated")
        self.assertEqual(len(set(ids)), 4)

        for sent, stored in zip(missions, queue["missions"]):
            self.assertEqual(stored["type"], sent["type"])
            self.assertEqual(stored["details"], sent["details"], "details round-trip exactly")
            self.assertEqual(stored["state"], "QUEUED")
            self.assertEqual(stored["robots"], [])
            self.assertEqual(stored["progress"], 0.0)
        first = queue["missions"][0]
        self.assertEqual((first["name"], first["priority"], first["return_home_policy"]), ("Přesná oblast", 3, 2))

    async def test_list_and_get_match_create(self):
        before = await self.client.queues()
        queue = await self.create()
        after = await self.client.queues()

        self.assertEqual(after["session_id"], before["session_id"])
        self.assertGreater(after["seq"], before["seq"])
        listed = [q for q in after["queues"] if q["queue_id"] == queue["queue_id"]]
        self.assertEqual(listed, [queue])
        self.assertEqual(await self.client.queue(queue["queue_id"]), queue)
        self.assertEqual(after["queues"][-1]["queue_id"], queue["queue_id"], "creation order")

    async def test_generated_queue_id(self):
        queue = await self.client.create_queue("batch", [load_mission("coverage.json")])
        self.created.append(queue["queue_id"])
        self.assertTrue(queue["queue_id"])
        self.assertEqual(queue["params"], {})

    async def test_duplicate_queue_id_conflicts(self):
        queue = await self.create()
        snapshot = await self.client.queues()
        await self.assert_refused(409, self.create(queue_id=queue["queue_id"], missions=[PRECISE_MISSION]))
        self.assertEqual(await self.client.queues(), snapshot, "nothing changed")

    async def test_invalid_queues_are_refused(self):
        before = await self.client.queues()
        good = load_mission("coverage.json")
        no_type = {k: v for k, v in good.items() if k != "type"}
        no_details = {k: v for k, v in good.items() if k != "details"}
        cases = {
            "missing scheduler": {"missions": [good]},
            "empty scheduler": {"scheduler": "", "missions": [good]},
            "no missions": {"scheduler": "batch"},
            "empty missions": {"scheduler": "batch", "missions": []},
            "missions not a list": {"scheduler": "batch", "missions": good},
            "mission without type": {"scheduler": "batch", "missions": [no_type]},
            "mission without details": {"scheduler": "batch", "missions": [no_details]},
            "details not an object": {"scheduler": "batch", "missions": [{**good, "details": "{}"}]},
            "duplicate mission ids": {"scheduler": "batch", "missions": [{**good, "id": "x"}, {**good, "id": "x"}]},
            "bad queue id": {"scheduler": "batch", "queue_id": "a b/c", "missions": [good]},
            "long queue id": {"scheduler": "batch", "queue_id": "q" * 65, "missions": [good]},
            "params not an object": {"scheduler": "batch", "params": "abort", "missions": [good]},
            "priority not an integer": {"scheduler": "batch", "missions": [{**good, "priority": "high"}]},
        }
        async with httpx.AsyncClient(base_url=self.client.base_url, timeout=10) as http:
            for label, body in cases.items():
                with self.subTest(label):
                    response = await http.post("/queues", json=body)
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertFalse(response.json()["success"])
                    self.assertTrue(response.json()["message"])
            for raw in ("not json", "[1, 2]", ""):
                with self.subTest(raw=raw):
                    response = await http.post("/queues", content=raw, headers={"Content-Type": "application/json"})
                    self.assertEqual(response.status_code, 400, response.text)

        self.assertEqual(await self.client.queues(), before, "nothing was stored")

    # | ----------------------- delete ----------------------- |

    async def test_delete(self):
        queue = await self.create()
        queue_id = queue["queue_id"]
        await self.client.delete_queue(queue_id)

        await self.assert_refused(404, self.client.queue(queue_id))
        await self.assert_refused(404, self.client.delete_queue(queue_id))
        self.assertNotIn(queue_id, await self.queue_ids())

        # The id can be used again.
        again = await self.create(queue_id=queue_id, missions=[PRECISE_MISSION])
        self.assertEqual(again["missions"][0]["id"], "precise")

    async def test_unknown_queue(self):
        missing = f"missing-{uuid.uuid4().hex[:8]}"
        await self.assert_refused(404, self.client.queue(missing))
        await self.assert_refused(404, self.client.delete_queue(missing))
        await self.assert_refused(404, self.client.submit_queue(missing))
        for command in ("start", "cancel", "pause", "resume"):
            await self.assert_refused(404, self.client.control_queue(missing, command))

    async def test_control_of_a_created_queue_conflicts(self):
        queue = await self.create()
        for command in ("start", "cancel", "pause", "resume"):
            with self.subTest(command):
                error = await self.assert_refused(409, self.client.control_queue(queue["queue_id"], command))
                self.assertIn("CREATED", error.message)
        self.assertEqual(await self.client.queue(queue["queue_id"]), queue)


    # | ----------------------- events ----------------------- |

    async def test_events_follow_the_store(self):
        url = f"{self.client.ws_base_url}/queues/events"
        async with websockets.connect(url) as ws:
            await asyncio.sleep(0.2)  # the bridge registers the connection asynchronously
            start = await self.client.queues()
            queue = await self.create(missions=[PRECISE_MISSION])
            await self.client.delete_queue(queue["queue_id"])

            created = json.loads(await asyncio.wait_for(ws.recv(), 5))
            removed = json.loads(await asyncio.wait_for(ws.recv(), 5))

        self.assertEqual(created["type"], "queue")
        self.assertEqual(created["session_id"], start["session_id"])
        self.assertEqual(created["seq"], start["seq"] + 1)
        self.assertEqual(created["queue"], queue, "the event carries the stored queue")

        self.assertEqual(removed, {"type": "queue_removed", "session_id": start["session_id"], "seq": start["seq"] + 2,
                                   "queue_id": queue["queue_id"]})
        self.assertEqual((await self.client.queues())["seq"], start["seq"] + 2)

    async def test_queue_events_iterator(self):
        events = []

        async def listen():
            async for event in self.client.queue_events():
                events.append(event)
                if event["type"] == "queue_removed":
                    return

        listener = asyncio.create_task(listen())
        await asyncio.sleep(0.3)
        queue = await self.create()
        await self.client.delete_queue(queue["queue_id"])
        await asyncio.wait_for(listener, 5)
        self.assertEqual([e["type"] for e in events], ["queue", "queue_removed"])
        self.assertEqual(events[0]["queue"], queue)


class QueueExecutionTest(QueueTestCase):
    """Submit (stage the first step), start, cancel: bridge + fleet manager + robots."""

    async def test_schedulers(self):
        self.assertEqual(sorted(await self.client.schedulers()), ["batch", "fleet"])

    async def test_submit_stages_only_the_first_mission(self):
        events = []

        async def listen():
            async for event in self.client.queue_events():
                if event["type"] == "queue" and event["queue"]["queue_id"] == queue_id:
                    events.append(event["queue"]["state"])

        queue_id = self.new_id()
        listener = asyncio.create_task(listen())
        await asyncio.sleep(0.3)
        await self.client.create_queue("batch", self.routes(2), queue_id=queue_id)
        submitted = await self.client.submit_queue(queue_id)
        self.assertIn(submitted["state"], ("UPLOADING", "READY"))

        ready = await self.wait_state(queue_id, "READY", "REJECTED")
        listener.cancel()
        self.assertEqual(ready["state"], "READY", ready["message"])
        first, second = ready["missions"]
        self.assertEqual((first["state"], first["robots"]), ("STAGED", [self.env.robot]))
        self.assertEqual(second["state"], "QUEUED", "later missions are not uploaded yet")
        self.assertEqual([s for i, s in enumerate(events) if i == 0 or events[i - 1] != s],
                         ["CREATED", "SUBMITTING", "UPLOADING", "READY"], "state changes seen on /queues/events")

        await self.assert_refused(409, self.client.delete_queue(queue_id))
        await self.assert_refused(409, self.client.submit_queue(queue_id))
        for command in ("pause", "resume"):
            await self.assert_refused(409, self.client.control_queue(queue_id, command))

    async def test_cancel_when_ready_unloads(self):
        ready = await self.submit_ready(self.routes(2))
        self.assertEqual(ready["state"], "READY", ready["message"])
        await self.client.control_queue(ready["queue_id"], "cancel")
        cancelled = await self.wait_state(ready["queue_id"], "CANCELLED", "FINISHED", timeout=30)
        self.assertEqual(cancelled["state"], "CANCELLED")
        self.assertEqual([m["state"] for m in cancelled["missions"]], ["CANCELLED", "CANCELLED"])

        # The robot is free again: the next queue stages.
        again = await self.submit_ready(self.routes(1))
        self.assertEqual(again["state"], "READY", again["message"])

    async def test_one_submitted_queue_at_a_time(self):
        first = await self.submit_ready(self.routes(1))
        self.assertEqual(first["state"], "READY", first["message"])
        second = await self.create(self.routes(1))

        error = await self.assert_refused(409, self.client.submit_queue(second["queue_id"]))
        self.assertIn(first["queue_id"], error.message)
        self.assertEqual((await self.client.queue(second["queue_id"]))["state"], "CREATED", "a refused submit changes nothing")

        await self.client.control_queue(first["queue_id"], "cancel")
        await self.wait_state(first["queue_id"], "CANCELLED")
        await self.client.submit_queue(second["queue_id"])
        self.assertEqual((await self.wait_state(second["queue_id"], "READY", "REJECTED"))["state"], "READY")

    async def test_unplannable_first_mission_is_rejected(self):
        queue = await self.create([route("uav9", 1, mission_id="unknown-robot"), *self.routes(1)])
        error = await self.assert_refused(400, self.client.submit_queue(queue["queue_id"]))
        self.assertIn("uav9", error.message)
        rejected = await self.client.queue(queue["queue_id"])
        self.assertEqual(rejected["state"], "REJECTED")
        self.assertEqual(rejected["message"], error.message)
        self.assertEqual([m["state"] for m in rejected["missions"]], ["QUEUED", "QUEUED"])

        # Rejected queues can be submitted again (and are refused the same way) or deleted.
        await self.assert_refused(400, self.client.submit_queue(queue["queue_id"]))
        await self.client.delete_queue(queue["queue_id"])

    async def test_refused_upload_rejects_the_queue(self):
        if not self.env.fake_robots:
            self.skipTest("needs fake robots (HOLOSWARM_TEST_STACK=ground) to refuse an upload")
        refused = await self.submit_ready([route(self.env.robot, 1, mission_id="refuse-upload-1"), *self.routes(1)])
        self.assertEqual(refused["state"], "REJECTED")
        self.assertIn(f"Upload to '{self.env.robot}' failed", refused["message"])
        self.assertEqual([m["state"] for m in refused["missions"]], ["QUEUED", "QUEUED"])

    async def test_started_queue_runs_to_the_end(self):
        if not self.env.can_run:
            self.skipTest(f"robots of HOLOSWARM_TEST_STACK={self.env.name} do not fly")
        ready = await self.submit_ready(self.routes(2))
        self.assertEqual(ready["state"], "READY", ready["message"])

        await self.client.control_queue(ready["queue_id"], "start")
        running = await self.wait_state(ready["queue_id"], "RUNNING", *TERMINAL)
        self.assertIn(running["state"], ("RUNNING", "FINISHED"))
        await self.assert_refused(409, self.client.control_queue(ready["queue_id"], "start"))

        done = await self.wait_state(ready["queue_id"], *TERMINAL, timeout=3 * self.env.mission_timeout)
        self.assertEqual(done["state"], "FINISHED", done["message"])
        for mission in done["missions"]:
            self.assertEqual((mission["state"], mission["progress"]), ("SUCCEEDED", 1.0), mission["message"])
        self.assertEqual(done["missions"][1]["robots"], [self.env.robot], "the second mission ran by itself")

    async def test_cancel_while_running(self):
        if not self.env.can_run:
            self.skipTest(f"robots of HOLOSWARM_TEST_STACK={self.env.name} do not fly")
        ready = await self.submit_ready(self.routes(2))
        await self.client.control_queue(ready["queue_id"], "start")
        await self.wait_state(ready["queue_id"], "RUNNING", *TERMINAL)
        await self.client.control_queue(ready["queue_id"], "cancel")

        done = await self.wait_state(ready["queue_id"], *TERMINAL, timeout=self.env.mission_timeout)
        self.assertEqual(done["state"], "CANCELLED")
        self.assertEqual(done["missions"][1]["state"], "CANCELLED", "the remaining mission is dropped")


class FleetManagerOutageTest(QueueTestCase):
    """The bridge keeps working when the fleet manager is down or restarts (its container is stopped/restarted)."""

    async def test_submit_without_fleet_manager_keeps_the_queue(self):
        queue = await self.create(self.routes(1))
        await asyncio.to_thread(STACK.stop_fleet_manager)
        try:
            with self.assertRaises(ApiUnavailable):
                await self.client.schedulers()
            with self.assertRaises(ApiUnavailable):
                await self.client.submit_queue(queue["queue_id"])

            after = await self.client.queue(queue["queue_id"])
            unchanged = {k: v for k, v in after.items() if k != "updated_at"}
            self.assertEqual(unchanged, {k: v for k, v in queue.items() if k != "updated_at"}, "still CREATED, unchanged")
            await self.assert_refused(409, self.client.control_queue(queue["queue_id"], "start"))
        finally:
            await asyncio.to_thread(STACK.start_fleet_manager)

        await self.submit_when_robots_known(queue["queue_id"])
        self.assertEqual((await self.wait_state(queue["queue_id"], "READY", "REJECTED"))["state"], "READY")

    async def test_fleet_manager_restart_rejects_the_staged_queue(self):
        ready = await self.submit_ready(self.routes(1))
        self.assertEqual(ready["state"], "READY", ready["message"])

        await asyncio.to_thread(STACK.restart_fleet_manager)
        rejected = await self.wait_state(ready["queue_id"], "REJECTED", "INTERRUPTED", timeout=30)
        self.assertEqual((rejected["state"], rejected["message"]), ("REJECTED", "Fleet manager restarted"))

        # The robot still holds the goal staged by the old fleet manager; submitting again works anyway
        # (once the new fleet manager has heard from the robot).
        await self.submit_when_robots_known(ready["queue_id"])
        again = await self.wait_state(ready["queue_id"], "READY", "REJECTED")
        self.assertEqual(again["state"], "READY", again["message"])



class WorkspaceTest(QueueTestCase):
    """Workspaces: mrs_terrain worlds, their orthophoto, and the queues stored in them."""

    async def test_default_workspace_holds_the_temesvar_worlds(self):
        listing = await self.client.workspaces()
        self.assertEqual(listing["default"], "temesvar")
        temesvar = next(w for w in listing["workspaces"] if w["name"] == "temesvar")
        self.assertIn("temesvar_field", temesvar["worlds"])
        self.assertIn("temesvar_river", temesvar["worlds"])
        self.assertTrue(all(name.startswith("temesvar") for name in temesvar["worlds"]))

    async def test_workspace_has_worlds_map_and_queues(self):
        queue = await self.create(self.routes(1), workspace="temesvar")
        workspace = await self.client.workspace("temesvar")

        self.assertEqual(sorted(w["name"] for w in workspace["worlds"]),
                         sorted(next(w for w in (await self.client.workspaces())["workspaces"] if w["name"] == "temesvar")["worlds"]))
        union = workspace["bounds"]
        for world in workspace["worlds"]:
            self.assertGreaterEqual(len(world["safety_area"]), 3, world["name"])
            self.assertIsNotNone(world["origin"], world["name"])
            for point in world["safety_area"]:
                self.assertTrue(union["south"] <= point["lat"] <= union["north"] and union["west"] <= point["lon"] <= union["east"])
        map_ = workspace["map"]
        self.assertEqual(map_["url"], "/workspaces/temesvar/map")
        self.assertTrue(map_["west"] < union["west"] and map_["east"] > union["east"]
                        and map_["south"] < union["south"] and map_["north"] > union["north"], "the map covers the worlds with a margin")

        self.assertIn(queue, workspace["queues"], "stored queues come with the workspace")
        self.assertEqual(queue["workspace"], "temesvar")
        listed = (await self.client.queues())["queues"]
        self.assertIn(queue["queue_id"], [q["queue_id"] for q in listed])
        async with httpx.AsyncClient(base_url=self.client.base_url, timeout=10) as http:
            other = (await http.get("/queues", params={"workspace": "elsewhere"})).json()["queues"]
        self.assertEqual(other, [])

    async def test_default_workspace_of_new_queues(self):
        queue = await self.create(self.routes(1))
        self.assertEqual(queue["workspace"], "temesvar")

    async def test_map_image(self):
        workspace = await self.client.workspace("temesvar")
        jpeg, bounds = await self.client.workspace_map("temesvar", max_px=1024)
        image = Image.open(io.BytesIO(jpeg))
        self.assertEqual(image.format, "JPEG")
        self.assertEqual(max(image.size), 1024)
        for got, expected in zip(bounds, (workspace["map"][k] for k in ("west", "south", "east", "north"))):
            self.assertAlmostEqual(got, expected, places=8)
        # Pixels are square in degrees (the RGB lattice), so the image has the extent's aspect in degrees.
        west, south, east, north = bounds
        self.assertAlmostEqual(image.size[0] / image.size[1], (east - west) / (north - south), delta=0.02 * image.size[0] / image.size[1])
        # It is a photo, not the empty background.
        colors = image.convert("RGB").resize((32, 32)).getcolors(1024)
        self.assertGreater(len(colors), 50)

        async with httpx.AsyncClient(base_url=self.client.base_url, timeout=10) as http:
            for bad in ("0", "abc", "100000"):
                with self.subTest(max_px=bad):
                    self.assertEqual((await http.get("/workspaces/temesvar/map", params={"max_px": bad})).status_code, 400)

    async def test_unknown_workspace(self):
        await self.assert_refused(404, self.client.workspace("nowhere"))
        await self.assert_refused(404, self.client.workspace_map("nowhere"))
        error = await self.assert_refused(400, self.create(self.routes(1), workspace="nowhere"))
        self.assertIn("nowhere", error.message)


class TerrainTest(QueueTestCase):
    """Terrain height and UTM coordinates of a point (bridge -> heightmap_sampler node)."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await asyncio.to_thread(wait_sampler)

    async def test_simulator_origin(self):
        # The GNSS origin of the simulator (compose/simulation/config/hw_api.yaml: utm_zone "33U", utm_x 473864.74,
        # utm_y 5548732.22, amsl 300.0) is the origin of config/worlds/world_bechovice.yaml.
        point = await self.client.terrain_height(50.090278, 14.634639)
        self.assertEqual((point["lat"], point["lon"]), (50.090278, 14.634639))
        self.assertEqual(point["utm"]["zone"], "33U")
        self.assertAlmostEqual(point["utm"]["x"], 473864.74, delta=0.01)
        self.assertAlmostEqual(point["utm"]["y"], 5548732.22, delta=0.01)
        self.assertTrue(point["valid"])
        self.assertAlmostEqual(point["amsl"], 300.0, delta=0.1)

    async def test_known_heights(self):
        # as `ros2 run heightmap_sampler sample_height LON LAT` gives them (bundled dataset, WGS84 ellipsoid)
        for lat, lon, height in ((50.0905258, 14.6327381, 303.905), (49.3625695, 14.2619165, 451.769)):
            point = await self.client.terrain_height(lat, lon)
            self.assertTrue(point["valid"], point)
            self.assertAlmostEqual(point["amsl"], height, delta=0.01)

    async def test_no_height_outside_the_dataset(self):
        point = await self.client.terrain_height(0.0, 0.0)
        self.assertFalse(point["valid"])
        self.assertIsNone(point["amsl"])
        self.assertIn("zone", point["utm"])

    async def test_bad_coordinates(self):
        async with httpx.AsyncClient(base_url=f"http://{BRIDGE}", timeout=10) as http:
            for query in ("lon=14.2", "lat=abc&lon=14.2", "lat=95&lon=14.2", "lat=49&lon=181", "lat=49.1x&lon=14"):
                response = await http.get(f"/terrain/height?{query}")
                self.assertEqual(response.status_code, 400, query)

    async def test_sampler_down(self):
        await asyncio.to_thread(STACK.stop_sampler)
        try:
            await self.assert_refused(503, self.client.terrain_height(50.0905258, 14.6327381))
        finally:
            await asyncio.to_thread(STACK.start_sampler)
        self.assertTrue((await self.client.terrain_height(50.0905258, 14.6327381))["valid"])


class BridgeRestartTest(QueueTestCase):
    """Queues are stored: a restarted bridge has them again (the bridge container is restarted)."""

    async def test_queues_survive_a_bridge_restart(self):
        created = await self.create([PRECISE_MISSION, *self.routes(1)], name="kept")
        staged = await self.submit_ready(self.routes(2))
        self.assertEqual(staged["state"], "READY", staged["message"])
        deleted = await self.create(self.routes(1))
        await self.client.delete_queue(deleted["queue_id"])

        await asyncio.to_thread(STACK.restart_bridge)

        after = await self.client.queue(created["queue_id"])
        self.assertEqual({k: v for k, v in after.items() if k != "updated_at"}, {k: v for k, v in created.items() if k != "updated_at"},
                         "a stored queue comes back unchanged (details exactly)")
        rejected = await self.client.queue(staged["queue_id"])
        self.assertEqual((rejected["state"], rejected["message"]), ("REJECTED", "Bridge restarted"))
        self.assertEqual([m["state"] for m in rejected["missions"]], ["QUEUED", "QUEUED"])
        await self.assert_refused(404, self.client.queue(deleted["queue_id"]))
        self.assertIn(created["queue_id"], [q["queue_id"] for q in (await self.client.workspace("temesvar"))["queues"]])

        # The bridge releases the queue it left staged on the fleet manager, so it can be submitted again.
        deadline = time.time() + 30
        while True:
            try:
                await self.submit_when_robots_known(staged["queue_id"])
                break
            except ApiError as exc:
                if exc.status != 409 or time.time() > deadline:
                    raise
                await asyncio.sleep(1.0)
        self.assertEqual((await self.wait_state(staged["queue_id"], "READY", "REJECTED"))["state"], "READY")


if __name__ == "__main__":
    unittest.main()
