import unittest

from holoswarm_client.data.execution import ExecutionStore, MissionState, QueueState


def mission(mid, state="QUEUED", progress=0.0):
    return {"id": mid, "name": "", "type": "WaypointPlanner", "details": {"robots": []}, "state": state,
            "robots": ["uav1"], "progress": progress, "message": "", "updated_at": 1.0}


def queue(qid="A", state="CREATED", missions=None, created_at=1.0, **extra):
    return {"queue_id": qid, "name": "", "scheduler": "batch", "params": {}, "world_id": "", "state": state,
            "message": "", "paused": False, "created_at": created_at, "updated_at": created_at,
            "missions": missions if missions is not None else [mission("m1"), mission("m2")], **extra}


def snapshot(seq=5, session="s1", queues=None):
    return {"success": True, "session_id": session, "seq": seq, "queues": queues if queues is not None else [queue()]}


def event(seq, session="s1", **q):
    return {"type": "queue", "session_id": session, "seq": seq, "queue": queue(**q)}


def removed(seq, qid="A", session="s1"):
    return {"type": "queue_removed", "session_id": session, "seq": seq, "queue_id": qid}


class ExecutionStoreTest(unittest.TestCase):

    def setUp(self):
        self.store = ExecutionStore()
        self.store.apply_snapshot(snapshot(), now=10.0)

    def test_snapshot(self):
        q = self.store.queue("A")
        self.assertEqual(q.state, QueueState.CREATED)
        self.assertEqual(list(q.missions), ["m1", "m2"])
        self.assertEqual(q.missions["m1"].details, {"robots": []})
        self.assertEqual(self.store.last_seq, 5)

    def test_snapshot_orders_by_creation(self):
        self.store.apply_snapshot(snapshot(queues=[queue("B", created_at=2.0), queue("A", created_at=1.0)]), now=0)
        self.assertEqual(list(self.store.queues), ["A", "B"])

    def test_event_replaces_the_queue(self):
        stale = self.store.apply_event(event(6, state="RUNNING", missions=[mission("m1", "EXECUTING", 0.5), mission("m2")]), now=11.0)
        self.assertFalse(stale)
        q = self.store.queue("A")
        self.assertEqual(q.state, QueueState.RUNNING)
        self.assertEqual(q.missions["m1"].state, MissionState.EXECUTING)
        self.assertEqual(q.missions["m1"].progress, 0.5)
        self.assertEqual(self.store.last_seq, 6)
        self.assertEqual(self.store.updated_at, 11.0)

    def test_new_queue_and_removal(self):
        self.assertFalse(self.store.apply_event(event(6, qid="B", created_at=3.0), now=0))
        self.assertEqual(list(self.store.queues), ["A", "B"])
        self.assertFalse(self.store.apply_event(removed(7, "A"), now=0))
        self.assertEqual(list(self.store.queues), ["B"])
        self.assertFalse(self.store.apply_event(removed(8, "missing"), now=0), "removing an unknown queue is harmless")

    def test_duplicates_ignored(self):
        self.assertFalse(self.store.apply_event(event(5, state="RUNNING"), now=0))
        self.assertEqual(self.store.queue("A").state, QueueState.CREATED)

    def test_gap_requests_refresh(self):
        self.assertTrue(self.store.apply_event(event(7, state="RUNNING"), now=0))
        self.assertEqual(self.store.queue("A").state, QueueState.CREATED)

    def test_new_session_requests_refresh(self):
        self.assertTrue(self.store.apply_event(event(6, session="s2"), now=0))

    def test_unknown_event_type_requests_refresh(self):
        self.assertTrue(self.store.apply_event({"type": "other", "session_id": "s1", "seq": 6}, now=0))

    def test_unknown_states(self):
        self.store.apply_event(event(6, state="SOMETHING", missions=[mission("m1", "WEIRD")]), now=0)
        self.assertEqual(self.store.queue("A").state, QueueState.UNKNOWN)
        self.assertEqual(self.store.queue("A").missions["m1"].state, MissionState.UNKNOWN)

    def test_in_flight(self):
        self.assertIsNone(self.store.in_flight())
        self.store.apply_event(event(6, qid="B", state="READY", created_at=2.0), now=0)
        self.assertEqual(self.store.in_flight().queue_id, "B")

    def test_state_rules(self):
        self.assertTrue(QueueState.CREATED.can_submit and QueueState.REJECTED.can_submit)
        self.assertFalse(QueueState.READY.can_submit)
        self.assertTrue(QueueState.READY.can_start)
        self.assertFalse(QueueState.UPLOADING.can_start)
        self.assertTrue(QueueState.UPLOADING.can_cancel)
        self.assertFalse(QueueState.CREATED.can_cancel)
        for state in (QueueState.CREATED, QueueState.REJECTED, QueueState.FINISHED, QueueState.CANCELLED, QueueState.INTERRUPTED):
            self.assertTrue(state.can_delete, state)
        for state in (QueueState.SUBMITTING, QueueState.UPLOADING, QueueState.READY, QueueState.RUNNING, QueueState.CANCELLING):
            self.assertFalse(state.can_delete, state)

    def test_notify_only_when_dirty(self):
        calls = []
        self.store.subscribe(lambda _: calls.append(1))
        self.store.notify()
        self.store.notify()
        self.assertEqual(len(calls), 1)
        self.store.mark_changed()
        self.store.notify()
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
