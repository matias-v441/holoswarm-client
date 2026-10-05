import asyncio
import threading
import unittest

from holoswarm_client.data.execution import ExecutionStore
from holoswarm_client.data.queue_draft import QueueDraft
from holoswarm_client.iroc.client import ApiError, ApiUnavailable, TransportError
from holoswarm_client.services.queues import CREATING, QueueService

COVERAGE = {"type": "CoveragePlanner", "details": {"robots": ["uav1"], "search_area": [{"x": 50.123456789, "y": 14.0}]}}
WAYPOINTS = {"type": "WaypointPlanner", "details": {"robots": [{"name": "uav1", "points": []}]}}


class FakeClient:
    """Queue API double. Failures are scripted per method as a list of exceptions consumed in order."""

    base_url = "http://fake:8080"
    ws_base_url = "ws://fake:8080"

    def __init__(self):
        self.queues_stored: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.fail: dict[str, list[Exception]] = {}
        self.gate: asyncio.Event | None = None  # holds requests until set

    async def _enter(self, name, *args):
        self.calls.append((name, *args))
        if self.gate is not None:
            await self.gate.wait()
        failures = self.fail.get(name)
        if failures:
            raise failures.pop(0)

    async def create_queue(self, scheduler, missions, queue_id=None, name=None, params=None, world_id=None, workspace=None):
        await self._enter("create_queue", scheduler, missions, queue_id, name, params)
        self.workspaces_used = getattr(self, "workspaces_used", []) + [workspace]
        queue = {"queue_id": queue_id, "scheduler": scheduler, "missions": missions}
        self.queues_stored[queue_id] = queue
        return queue

    async def queue(self, queue_id):
        await self._enter("queue", queue_id)
        if queue_id not in self.queues_stored:
            raise ApiError(404, f"Queue '{queue_id}' not found")
        return self.queues_stored[queue_id]

    async def submit_queue(self, queue_id):
        await self._enter("submit_queue", queue_id)
        return {"queue_id": queue_id}

    async def control_queue(self, queue_id, command):
        await self._enter("control_queue", queue_id, command)
        return {"success": True}

    async def delete_queue(self, queue_id):
        await self._enter("delete_queue", queue_id)

    async def queues(self):
        return {"session_id": "s1", "seq": 0, "queues": []}

    async def schedulers(self):
        return ["batch", "fleet"]

    def count(self, name):
        return sum(1 for call in self.calls if call[0] == name)


class QueueServiceTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.thread = threading.Thread(target=cls.loop.run_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.loop.call_soon_threadsafe(cls.loop.stop)
        cls.thread.join(timeout=5)

    def setUp(self):
        self.client = FakeClient()
        self.executions = ExecutionStore()
        self.draft = QueueDraft()
        self.service = QueueService(self.client, self.loop, self.executions, self.draft)
        self.messages = []
        self.service.on_message(lambda text, ok: self.messages.append((text, ok)))

    def run_op(self, future):
        self.assertIsNotNone(future)
        result = future.result(timeout=5)
        self.service.process_events()
        return result

    def fill_draft(self):
        return [self.draft.add(COVERAGE, "area"), self.draft.add(WAYPOINTS, "route")]

    # | ----------------------- create ----------------------- |

    def test_create_sends_the_whole_draft_once(self):
        added = self.fill_draft()
        self.assertTrue(self.run_op(self.service.create("batch", name="Survey", params={"abort_on_failure": True})))

        (call,) = [c for c in self.client.calls if c[0] == "create_queue"]
        _, scheduler, missions, queue_id, name, params = call
        self.assertEqual(scheduler, "batch")
        self.assertEqual(name, "Survey")
        self.assertEqual(params, {"abort_on_failure": True})
        self.assertTrue(queue_id.startswith("q-"))
        self.assertEqual(missions, [m.to_wire() for m in added], "every mission, in order, with its id")
        self.assertEqual(len(self.draft), 0, "the draft is emptied once the bridge has the queue")
        self.assertTrue(self.messages[-1][1])
        self.assertNotIn(CREATING, self.service.pending)

    def test_create_stores_the_queue_in_the_workspace(self):
        service = QueueService(self.client, self.loop, self.executions, self.draft, workspace="temesvar")
        self.fill_draft()
        self.assertTrue(self.run_op_with(service, service.create("batch")))
        self.assertEqual(self.client.workspaces_used, ["temesvar"])

        self.fill_draft()
        self.run_op(self.service.create("batch"))
        self.assertEqual(self.client.workspaces_used[-1], None, "no workspace given: the bridge's default")

    def run_op_with(self, service, future):
        result = future.result(timeout=5)
        service.process_events()
        return result

    def test_create_with_empty_draft(self):
        self.assertIsNone(self.service.create("batch"))
        self.assertEqual(self.client.count("create_queue"), 0)
        self.assertFalse(self.messages[-1][1])

    def test_refused_create_keeps_the_draft(self):
        self.fill_draft()
        self.client.fail["create_queue"] = [ApiError(400, "Bad request: mission 1: missing 'type'")]
        self.assertFalse(self.run_op(self.service.create("batch")))
        self.assertEqual(len(self.draft), 2)
        self.assertIn("missing 'type'", self.messages[-1][0])
        self.assertFalse(self.messages[-1][1])

    def test_unanswered_create_checks_whether_the_queue_exists(self):
        self.fill_draft()

        # Stored although the answer was lost.
        async def lost_answer(*args, **kwargs):
            self.client.calls.append(("create_queue",))
            self.client.queues_stored[kwargs["queue_id"]] = {}
            raise TransportError("timed out")

        original = self.client.create_queue
        self.client.create_queue = lost_answer
        self.assertTrue(self.run_op(self.service.create("batch")))
        self.assertEqual(self.client.count("queue"), 1)
        self.assertEqual(len(self.draft), 0)

        # Not stored.
        self.client.create_queue = original
        self.fill_draft()
        self.client.fail["create_queue"] = [TransportError("refused")]
        self.assertFalse(self.run_op(self.service.create("batch")))
        self.assertEqual(len(self.draft), 2)
        self.assertIn("not created", self.messages[-1][0])

        # Unknown: the lookup fails too.
        self.client.fail["create_queue"] = [TransportError("timed out")]
        self.client.fail["queue"] = [TransportError("timed out")]
        self.assertFalse(self.run_op(self.service.create("batch")))
        self.assertIn("outcome unknown", self.messages[-1][0])
        self.assertEqual(len(self.draft), 2)

    def test_missions_added_while_creating_stay_in_the_draft(self):
        self.fill_draft()
        self.client.gate = asyncio.Event()
        future = self.service.create("batch")
        self.assertIn(CREATING, self.service.pending)
        self.assertIsNone(self.service.create("batch"), "one creation at a time")
        late = self.draft.add(COVERAGE)
        self.loop.call_soon_threadsafe(self.client.gate.set)
        self.run_op(future)
        self.assertEqual([m.mission_id for m in self.draft.missions], [late.mission_id])

    # | ----------------------- queue operations ----------------------- |

    def test_submit_control_delete(self):
        self.run_op(self.service.submit("q1"))
        self.run_op(self.service.control("q1", "start"))
        self.run_op(self.service.delete("q1"))
        names = [c[0] for c in self.client.calls]
        self.assertEqual(names, ["submit_queue", "control_queue", "delete_queue"])
        self.assertTrue(all(ok for _, ok in self.messages))

    def test_failures_are_reported(self):
        self.client.fail["submit_queue"] = [ApiError(400, "Mission 'm1': Planning failed: robot uav1 is offline")]
        self.run_op(self.service.submit("q1"))
        self.assertIn("uav1 is offline", self.messages[-1][0])
        self.assertFalse(self.messages[-1][1])

        self.client.fail["submit_queue"] = [ApiUnavailable(503, "Fleet manager service 'submit_queue' is not available")]
        self.run_op(self.service.submit("q1"))
        self.assertIn("not available", self.messages[-1][0])

        self.client.fail["control_queue"] = [TransportError("timed out")]
        self.run_op(self.service.control("q1", "cancel"))
        self.assertIn("no answer", self.messages[-1][0])

    def test_one_request_per_queue(self):
        self.client.gate = asyncio.Event()
        changes = []
        self.executions.subscribe(lambda _: changes.append(set(self.service.pending)))
        future = self.service.submit("q1")
        self.assertIn("q1", self.service.pending)
        self.assertIsNone(self.service.control("q1", "start"))
        other = self.service.delete("q2")
        self.assertIsNotNone(other, "other queues are independent")
        self.service.process_events()
        self.assertEqual(changes[-1], {"q1", "q2"})

        self.loop.call_soon_threadsafe(self.client.gate.set)
        self.run_op(future)
        self.run_op(other)
        self.assertEqual(self.service.pending, set())
        self.assertEqual(changes[-1], set(), "observers see the request finish")
        self.assertEqual(self.client.count("control_queue"), 0)


if __name__ == "__main__":
    unittest.main()
