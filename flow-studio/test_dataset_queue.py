import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from uuid import uuid4

from dataset_queue import DatasetQueue, attempt_key


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.graph = {"id": "hand-seg", "version": 2, "nodes": [{"implementation": {"code": "test only"}}]}
        self.items = [{"dataset": {"selection": {"id": name, "sourceRoot": "second"}, "fingerprint": name},
                       "out": "/synthetic/" + name, "flags": {"check": True}} for name in ("A", "B", "C")]
        self.states = {}
        self.calls = []
        self.request = str(uuid4())
        self.q = DatasetQueue(self.base, self.launch, lambda: self.states)

    def launch(self, graph, item):
        self.assertNotIn("status", item)
        self.assertEqual(graph, self.graph)
        self.calls.append(item["out"])
        disk = json.loads((self.base / (self.request + ".json")).read_text(encoding="utf-8"))
        self.assertTrue(any(i["out"] == item["out"] and i["status"] == "launching" for i in disk["items"]))
        return {"output": item["out"]}

    def test_serial_order_and_frozen_public_projection(self):
        result = self.q.submit(self.request, self.graph, self.items)
        self.assertNotIn("graph", result)
        self.assertNotIn("flags", result["items"][0])
        self.assertNotIn("fingerprint", result["items"][0]["selection"])
        result["items"][0]["selection"]["id"] = "modified in caller"
        self.assertEqual(self.q.list()[0]["items"][0]["selection"]["id"], "A")
        for i, item in enumerate(self.items):
            out = item["out"]
            self.q._step()
            self.q._step()  # snapshot can lag even after launch returned
            self.assertEqual(self.calls, [x["out"] for x in self.items[:i + 1]])
            for status in ("pending", "unrecognized", "running", "completed"):
                self.states[out] = status
                self.q._step()
            self.assertEqual(self.q.list()[0]["items"][i]["status"], "completed")
        self.assertEqual(self.q.list()[0]["status"], "completed")

    def test_concurrent_submissions_and_exact_replay_skip_preflight(self):
        entered = threading.Event()
        release = threading.Event()
        checks = []
        answers = []
        def preflight():
            checks.append(True)
            entered.set()
            release.wait(3)
        def post():
            answers.append(self.q.submit(self.request, self.graph, self.items, preflight=preflight))
        threads = [threading.Thread(target=post) for _ in range(2)]
        threads[0].start()
        self.assertTrue(entered.wait(2))
        threads[1].start()
        release.set()
        for thread in threads:
            thread.join(2)
        self.assertEqual(len(answers), 2)
        self.assertEqual(answers[0], answers[1])
        self.assertEqual(len(checks), 1)
        self.q._step()
        again = DatasetQueue(self.base, self.launch, lambda: self.states)
        self.assertEqual(again.submit(self.request, self.graph, self.items, preflight=self.fail)["status"], "running")
        with self.assertRaises(ValueError):
            again.submit(self.request, self.graph, self.items[:1], preflight=self.fail)
        with self.assertRaises(ValueError):
            again.submit(str(uuid4()), self.graph, self.items, preflight=self.fail)

    def test_reordered_waiting_items_survive_restart(self):
        self.q.submit(self.request, self.graph, self.items)
        self.q._step()
        expected = [item["out"] for item in self.items[1:]]
        self.q.reorder({"expected": expected, "order": expected[::-1]})
        restored = DatasetQueue(self.base, self.launch, lambda: self.states)
        self.assertEqual(restored.view()["queueOrder"], expected[::-1])
        self.states[self.items[0]["out"]] = "completed"
        restored._step()
        restored._step()
        self.assertEqual(self.calls, [self.items[0]["out"], self.items[2]["out"]])

    def test_continuation_waits_and_ignores_previous_failure_at_same_output(self):
        out = self.items[0]["out"]
        continuation = str(uuid4())
        calls = []
        q = DatasetQueue(self.base, lambda g, item: calls.append(item) or {"output": item["out"]},
                         lambda: self.states)
        q.submit(self.request, self.graph, self.items[:1])
        q._step()
        self.states[out] = "failed"
        q._step()
        q.submit(continuation, self.graph, [{"out": out, "input": "/old/segments.jsonl", "resumeAttempt": continuation}])
        self.states["/other"] = "running"
        q._step()
        self.assertEqual(len(calls), 1)
        self.states["/other"] = "completed"
        q._step()
        q._step()
        self.assertEqual(len(calls), 2)
        self.states[attempt_key(out, continuation)] = "completed"
        q._step()
        self.assertEqual(q.list()[0]["status"], "completed")

    def test_failure_and_interruption_pause_remainder_and_survive_restart(self):
        for reason in ("failed", "interrupted"):
            with self.subTest(reason=reason):
                directory = self.base / reason
                q = DatasetQueue(directory, lambda g, item: {"output": item["out"]}, lambda: self.states)
                self.states.clear()
                q.submit(self.request, self.graph, self.items)
                q._step()
                self.states[self.items[0]["out"]] = reason
                q._step()
                restored = DatasetQueue(directory, self.fail, self.fail)
                restored._step()
                report = restored.list()[0]
                self.assertEqual(report["status"], "paused")
                self.assertEqual([i["status"] for i in report["items"]], [reason, "paused", "paused"])

    def test_unavailable_status_or_ambiguous_ack_does_not_start_next_item(self):
        def unknown():
            raise OSError("status unavailable")
        def ambiguous(g, item):
            self.calls.append(item["out"])
            raise TimeoutError("launch acknowledgement lost")
        q = DatasetQueue(self.base, ambiguous, unknown)
        q.submit(self.request, self.graph, self.items)
        with self.assertRaises(OSError):
            q._step()
        self.assertEqual(self.calls, [])
        q.statuses = lambda: {}
        with self.assertRaises(TimeoutError):
            q._step()
        self.assertEqual(q.list()[0]["items"][0]["status"], "launching")
        # Service restarted between accepting the job remotely and receiving acknowledgement.
        self.states[self.items[0]["out"]] = "running"
        recovered = DatasetQueue(self.base, self.launch, lambda: self.states)
        recovered._step()
        self.states.clear()
        recovered._step()
        self.assertEqual(self.calls, [self.items[0]["out"]])
        self.states[self.items[0]["out"]] = "completed"
        recovered._step()
        recovered._step()
        self.assertEqual(self.calls, [i["out"] for i in self.items[:2]])

    def test_definitive_rejection_or_existing_output_never_runs_remainder(self):
        def reject(g, item):
            raise ValueError("材料指纹已变化")
        self.q.launch = reject
        self.q.submit(self.request, self.graph, self.items)
        self.q._step()
        report = self.q.list()[0]
        self.assertEqual(report["items"][0]["error"], "材料指纹已变化")
        self.assertTrue(all(i["status"] == "paused" for i in report["items"][1:]))

        others = [dict(self.items[0], out="/already-taken")]
        self.states["/already-taken"] = "completed"
        self.q.launch = self.fail
        self.q.submit(str(uuid4()), self.graph, others)
        self.q._step()
        self.assertEqual(self.q.list()[0]["status"], "paused")

    def test_failed_preflight_leaves_no_queue_and_worker_recovers_existing_jobs(self):
        def reject():
            raise ValueError("missing input")
        with self.assertRaisesRegex(ValueError, "missing input"):
            self.q.submit(self.request, self.graph, self.items, preflight=reject)
        self.assertEqual(self.q.list(), [])
        self.q.submit(self.request, self.graph, self.items)
        done = threading.Event()
        def launch(g, item):
            self.calls.append(item["out"])
            self.states[item["out"]] = "completed"
            return {"output": item["out"]}
        q = DatasetQueue(self.base, launch, lambda: self.states, poll_interval=.01)
        q.start()
        q.start()
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and q.list()[0]["status"] != "completed":
                done.wait(.01)
            self.assertEqual(q.list()[0]["status"], "completed")
            self.assertEqual(self.calls, [i["out"] for i in self.items])
        finally:
            q.stop()


if __name__ == "__main__":
    unittest.main()
