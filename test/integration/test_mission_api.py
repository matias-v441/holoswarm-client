"""Original single-mission API of the bridge (/mission, outside the queues), driven through IROCClient against the compose stack.

POST /mission stages one mission on its robots, POST /mission/{start|pause|stop|unload} controls it and
/mission/feedback reports it. The fleet manager runs either this mission or the queues, never both.

The stack is started and stopped like for test_queue_api.py (HOLOSWARM_TEST_STACK, see stack.py and README.md).
"""
import asyncio
import json
import time
import unittest
import uuid

import httpx
import websockets

from holoswarm_client.iroc.client import ApiError, IROCClient

from stack import BRIDGE, STACK, Environment, route

ENV: Environment | None = None


def done(message: dict) -> bool:
    """The last feedback of a mission (its result) carries success; progress feedback does not."""
    return "success" in message


def running(message: dict) -> bool:
    return not done(message)


def setUpModule():
    global ENV
    ENV = STACK.start()  # shared with the other test modules; stopped when the run ends


def single(robot: str, index: int = 1) -> dict:
    """A short waypoint mission near the robot's spawn point, as POST /mission takes it."""
    mission = route(robot, index)
    return {"type": mission["type"], "uuid": f"it-{uuid.uuid4().hex[:12]}", "details": mission["details"]}


class MissionTestCase(unittest.IsolatedAsyncioTestCase):
    """Leaves the fleet without a single mission afterwards (stopped or unloaded), and removes the queues it created."""

    async def asyncSetUp(self):
        # See QueueTestCase: a new HTTP client per request is slower than debug mode's 0.1 s.
        asyncio.get_running_loop().slow_callback_duration = 1.0
        self.client = IROCClient(BRIDGE)
        self.env = ENV
        self.created: list[str] = []

    async def asyncTearDown(self):
        await self.release()
        for queue_id in self.created:
            try:
                queue = await self.client.queue(queue_id)
                if queue["state"] in ("UPLOADING", "READY", "RUNNING"):
                    await self.client.control_queue(queue_id, "cancel")
                    await self.wait_queue(queue_id, "FINISHED", "CANCELLED", "REJECTED", "INTERRUPTED")
                await self.client.delete_queue(queue_id)
            except ApiError:
                pass

    async def release(self) -> None:
        """Stop (or unload) the single mission, if any, and wait until the fleet manager has no active mission."""
        async with httpx.AsyncClient(base_url=self.client.base_url, timeout=20) as http:
            await http.post("/mission/stop")
            deadline = time.time() + max(30.0, self.env.mission_timeout)
            # GET /mission answers 200 only while a mission is executing or paused.
            while (await http.get("/mission")).status_code == 200:
                if time.time() > deadline:
                    self.fail("the single mission did not stop")
                await asyncio.sleep(0.3)

    async def assert_refused(self, status: int, coro) -> ApiError:
        with self.assertRaises(ApiError) as caught:
            await coro
        self.assertEqual(caught.exception.status, status, caught.exception.message)
        self.assertTrue(caught.exception.message)
        return caught.exception

    async def wait_queue(self, queue_id: str, *states: str, timeout: float = 30.0) -> dict:
        deadline = time.time() + timeout
        while True:
            queue = await self.client.queue(queue_id)
            if queue["state"] in states or time.time() > deadline:
                return queue
            await asyncio.sleep(0.3)

    async def send(self, mission: dict | None = None) -> dict:
        body = await self.client.send_mission(mission or single(self.env.robot))
        self.assertTrue(body["success"], body)
        return body

    async def feedback(self, ws, until, timeout: float) -> list[dict]:
        """/mission/feedback messages up to the first one until() accepts (included)."""
        messages = []
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                self.fail(f"no matching feedback within {timeout:.0f} s; got {[m.get('mission_state') for m in messages]}")
            try:
                message = json.loads(await asyncio.wait_for(ws.recv(), remaining))
            except asyncio.TimeoutError:
                continue
            messages.append(message)
            if until(message):
                return messages


class MissionUploadTest(MissionTestCase):
    """Staging, unloading and refusing a single mission: the robots are given the goal, nothing moves."""

    async def test_send_stages_on_the_robots(self):
        body = await self.send()
        self.assertTrue(body["message"])
        self.assertEqual([(r["robot"], r["success"]) for r in body["robot_results"]], [(self.env.robot, True)])

        # Staged, not executing: only one mission at a time, and nothing to return yet.
        error = await self.assert_refused(409, self.client.send_mission(single(self.env.robot, 2)))
        self.assertIn("staged", error.message)
        self.assertEqual([(r["robot"], r["success"]) for r in error.body["robot_results"]], [(self.env.robot, False)])
        async with httpx.AsyncClient(base_url=self.client.base_url, timeout=20) as http:
            self.assertNotEqual((await http.get("/mission")).status_code, 200)

    async def test_stop_unloads_the_staged_mission(self):
        await self.send()
        stopped = await self.client.control_mission("stop")
        self.assertTrue(stopped["success"], stopped)
        self.assertIn("unloaded", stopped["message"])
        self.assertEqual([(r["robot"], r["success"]) for r in stopped["robot_results"]], [(self.env.robot, True)])
        await self.send()  # the robot is free again

    async def test_unload_releases_the_staged_mission(self):
        await self.send()
        unloaded = await self.client.control_mission("unload")
        self.assertTrue(unloaded["success"], unloaded)
        await self.send()

    async def test_invalid_missions_are_refused(self):
        good = single(self.env.robot)
        cases = {
            "missing type": {k: v for k, v in good.items() if k != "type"},
            "missing details": {k: v for k, v in good.items() if k != "details"},
            "unknown planner": {**good, "type": "NoSuchPlanner"},
            "unknown robot": single("uav9"),
        }
        async with httpx.AsyncClient(base_url=self.client.base_url, timeout=20) as http:
            for label, body in cases.items():
                with self.subTest(label):
                    response = await http.post("/mission", json=body)
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertTrue(response.json()["message"])
            for raw in ("not json", "[1, 2]", ""):
                with self.subTest(raw=raw):
                    response = await http.post("/mission", content=raw, headers={"Content-Type": "application/json"})
                    self.assertEqual(response.status_code, 400, response.text)
        await self.send()  # nothing was staged

    async def test_controls_without_a_mission(self):
        error = await self.assert_refused(409, self.client.control_mission("start"))
        self.assertIn("rejected", error.message)
        for command in ("pause", "stop", "unload"):
            with self.subTest(command):
                error = await self.assert_refused(409, self.client.control_mission(command))
                self.assertIn("No active mission", error.message)
        with self.assertRaises(ValueError):
            await self.client.control_mission("fly")
        async with httpx.AsyncClient(base_url=self.client.base_url, timeout=20) as http:
            self.assertEqual((await http.post("/mission/fly")).status_code, 400)
            self.assertEqual((await http.post("/robots/uav9/mission/start")).status_code, 404)
            self.assertEqual((await http.post(f"/robots/{self.env.robot}/mission/fly")).status_code, 400)


class MissionAndQueuesTest(MissionTestCase):
    """The fleet manager runs either the single mission or the queues."""

    async def create_queue(self) -> str:
        queue_id = f"it-{uuid.uuid4().hex[:12]}"
        self.created.append(queue_id)
        await self.client.create_queue("batch", [route(self.env.robot, 1)], queue_id=queue_id)
        return queue_id

    async def test_sent_mission_blocks_queue_submit(self):
        await self.send()
        queue_id = await self.create_queue()
        error = await self.assert_refused(409, self.client.submit_queue(queue_id))
        self.assertIn("outside the queues", error.message)

        await self.client.control_mission("stop")
        await self.client.submit_queue(queue_id)
        ready = await self.wait_queue(queue_id, "READY", "REJECTED")
        self.assertEqual(ready["state"], "READY", ready["message"])

    async def test_submitted_queue_blocks_send(self):
        queue_id = await self.create_queue()
        await self.client.submit_queue(queue_id)
        ready = await self.wait_queue(queue_id, "READY", "REJECTED")
        self.assertEqual(ready["state"], "READY", ready["message"])

        error = await self.assert_refused(409, self.client.send_mission(single(self.env.robot)))
        self.assertIn("queues are busy", error.message)

        await self.client.control_queue(queue_id, "cancel")
        self.assertEqual((await self.wait_queue(queue_id, "CANCELLED"))["state"], "CANCELLED")
        await self.send()


class MissionRunTest(MissionTestCase):
    """Starting a sent mission: the robots execute it and /mission/feedback reports it."""

    def setUp(self):
        if not ENV.can_run:
            self.skipTest(f"robots of HOLOSWARM_TEST_STACK={ENV.name} do not fly")

    async def start_mission(self, ws) -> dict:
        """Send and start a mission; returns it at its first progress feedback."""
        mission = single(self.env.robot)
        await self.send(mission)
        started = await self.client.control_mission("start")
        self.assertTrue(started["success"], started)
        await self.feedback(ws, running, timeout=30)
        return mission

    async def test_started_mission_runs_to_the_end(self):
        async with websockets.connect(f"{self.client.ws_base_url}/mission/feedback") as ws:
            await asyncio.sleep(0.2)  # the bridge registers the connection asynchronously
            mission = await self.start_mission(ws)
            await self.assert_refused(409, self.client.send_mission(single(self.env.robot, 2)))
            async with httpx.AsyncClient(base_url=self.client.base_url, timeout=20) as http:
                goal = (await http.get("/mission")).json()
            messages = await self.feedback(ws, done, timeout=3 * self.env.mission_timeout)

        self.assertEqual((goal["type"], goal["uuid"]), (mission["type"], mission["uuid"]), "GET /mission returns the running mission")
        self.assertEqual([r["robot"] for r in goal["robot_data"]], [self.env.robot])

        result = messages[-1]
        self.assertEqual(result["mission_state"], "mission_completed", result["message"])
        self.assertTrue(result["success"])
        self.assertEqual(result["progress"], 100.0)
        self.assertEqual([(r["robot"], r["success"]) for r in result["robots"]], [(self.env.robot, True)])
        progress = [r for m in messages[:-1] for r in m["robots"] if r["robot"] == self.env.robot]
        self.assertTrue(progress, "per-robot progress while it runs")
        await self.release()
        await self.send()  # the fleet is free again

    async def test_stop_while_running(self):
        async with websockets.connect(f"{self.client.ws_base_url}/mission/feedback") as ws:
            await asyncio.sleep(0.2)
            await self.start_mission(ws)
            stopped = await self.client.control_mission("stop")
            self.assertTrue(stopped["success"], stopped)
            result = (await self.feedback(ws, done, timeout=self.env.mission_timeout))[-1]
        self.assertIn(result["mission_state"], ("mission_canceled", "mission_aborted"), result["message"])
        self.assertFalse(result["success"])
        await self.release()
        await self.send()

    async def test_pause_and_resume(self):
        if not self.env.flying:
            self.skipTest("fake robots finish a goal in 2 s and do not pause; needs HOLOSWARM_TEST_STACK=flight")
        async with websockets.connect(f"{self.client.ws_base_url}/mission/feedback") as ws:
            await asyncio.sleep(0.2)
            await self.start_mission(ws)
            paused = await self.client.control_mission("pause")
            self.assertTrue(paused["success"], paused)
            resumed = await self.client.control_mission("start")
            self.assertTrue(resumed["success"], resumed)
            result = (await self.feedback(ws, done, timeout=3 * self.env.mission_timeout))[-1]
        self.assertEqual(result["mission_state"], "mission_completed", result["message"])


if __name__ == "__main__":
    unittest.main()
