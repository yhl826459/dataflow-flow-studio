"""Exercise the direct Linux service and its real HTTP and storage paths with synthetic runs."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from functools import partial
from http.server import ThreadingHTTPServer
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4
from unittest.mock import patch

import dataset_queue
import local_server as app


class LaunchTest(unittest.TestCase):
    def test_unchanged_native_copy_uses_batch_runner_but_real_edits_are_frozen(self):
        import run_progress
        from operator_sources import source_for
        project = Path(app.__file__).resolve().parents[1]
        baseline = source_for(project, "refine", "native")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            source = home / "clips.jsonl"
            source.write_text('{"task":"objects"}\n')
            with (patch.object(Path, "home", return_value=home), patch.object(Path, "cwd", return_value=project),
                  patch.object(run_progress, "STATE", home/"state"),
                  patch.object(app.subprocess, "Popen", return_value=SimpleNamespace(pid=123)) as spawn):
                for changed in (False, True):
                    impl = dict(kind="native", code=baseline["code"]+("# saved edit\n" if changed else ""),
                                baseHash=baseline["baseHash"])
                    graph = dict(id="hand-seg", nodes=[dict(type="input",config={"task":"objects"}),
                                                       dict(type="refine",implementation=impl)])
                    launch = app.remote_launch(dict(graph=graph, flags=["--workbench-fuse"], dataset={},
                                                    input=str(source), out=str(home/str(changed))))
                    work = home/"state/workbench"/launch["requestId"]
                    self.assertEqual((work/"source").exists(), changed)
                    nodes = json.loads((work/"launch.json").read_text())["graph"]["nodes"]
                    self.assertEqual("implementation" in nodes[-1], changed)
                    self.assertEqual(spawn.call_args.kwargs["env"]["FLOW_STUDIO_NATIVE_OVERRIDES"], "refine" if changed else "")
                    self.assertIn("--workbench-compact", spawn.call_args.args[0])

    def test_named_segmentation_ports_and_monitor(self):
        nodes = [dict(id=kind, type=kind) for kind in ("input","prompt","segment","output")]
        rgb = dict(id="video-wire", **{"from":"input","to":"segment","targetPort":"rgb"})
        target = dict(id="phrase-wire", **{"from":"prompt","to":"segment","targetPort":"targets"})
        output = dict(id="mask-wire", **{"from":"segment","to":"output"})
        graph = dict(id="manual", version=4, nodes=nodes, edges=[rgb,target,output])
        self.assertIn("--prompt-zh=", app.compile_graph(graph, True))
        for edges in ([target,output], [rgb,output], [dict(rgb,targetPort="targets"),target,output],
                      [rgb,target,dict(target,id="extra"),output]):
            with self.assertRaises(ValueError):
                app.compile_graph(dict(graph, edges=edges), True)
            with self.assertRaises(ValueError):
                app.validate_library(dict(graphs=[dict(graph,edges=edges)], drafts={}, custom=[]))
        stages = {"prompt":{"started":1,"status":"running"}, "genception":{"status":"pending"}}
        state = dict(id="run", output="/tmp/test", started=1, status="running", has_input=True,
                     rounds=[dict(number=0,stages=stages)], receipt=dict(graph=graph,answer={"log":""}))
        self.assertEqual(app.convert(state)["activeEdges"], [rgb["id"]])
        stages["prompt"]["status"]="failed"
        self.assertEqual(app.convert(state)["activeEdges"], [rgb["id"]])
        stages["prompt"].update(status="done",finished=2)
        stages["genception"].update(status="running",started=3)
        self.assertEqual(app.convert(state)["activeEdges"], [rgb["id"],target["id"]])

    def test_abs_resume_uses_the_api_failure_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp).resolve()
            clip = out / "part-1"
            for file in (out / "dataflow_step2.jsonl", clip / "abs_inputs/job.json",
                         clip / "abs_inputs/config.json", clip / "Absolute Score/_run/manifest.json"):
                file.parent.mkdir(parents=True, exist_ok=True)
                file.touch()
            log = clip / "abs.log"
            state = dict(id="run", status="failed", rounds=[
                dict(number=0, stages={"abs": dict(status="failed", sequence=clip.name, log=str(log))})
            ])
            record = dict(graph={"nodes": []}, answer={"output": str(out)})
            for message in ("Connection error.", "Connection refused", "Error code: 429", "Error code: 503"):
                with self.subTest(message=message):
                    log.write_text(message)
                    self.assertEqual(app.resume_candidate(state, record),
                                     dict(stateId="run", group=None, sequence=clip.name))
            log.write_text("Error code: 401")
            self.assertIsNone(app.resume_candidate(state, record))

    def test_abs_resume_checks_hidden_runtime_in_compact_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp).resolve()
            work = out / ".run" / "first-part"
            clip = work / "part-1"
            for file in (work / "dataflow_step2.jsonl", clip / "abs_inputs/job.json",
                         clip / "abs_inputs/config.json", clip / "Absolute Score/_run/manifest.json"):
                file.parent.mkdir(parents=True, exist_ok=True)
                file.touch()
            log = clip / "abs.log"
            log.write_text("Error code: 503")
            state = dict(id="run", status="failed", rounds=[
                dict(number=0, group="first-part", stages={"abs": dict(status="failed", sequence=clip.name, log=str(log))})
            ])
            record = dict(graph={"nodes": []}, answer={"output": str(out)})
            self.assertEqual(app.resume_candidate(state, record),
                             dict(stateId="run", group="first-part", sequence=clip.name))

    def test_launch_with_abs_reads_configuration_before_spawning(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            config = home / "scoring_scripts/abs/api_config.json"
            config.parent.mkdir(parents=True)
            config.write_text('{"api":{"key_env":"FLOW_STUDIO_TEST_ABS_KEY"}}', encoding="utf-8")
            source = home / "input.jsonl"
            source.write_text('{"task":"objects"}\n', encoding="utf-8")
            output = home / "result"
            graph = {"nodes": [{"type":"input", "config":{"task":"objects"}}]}
            progress = SimpleNamespace(STATE=home / "state", process_start=lambda pid: "simulated")
            payload = dict(graph=graph, input=str(source), out=str(output), flags=["--abs"])
            with (patch.object(Path, "home", return_value=home),
                  patch.dict(sys.modules, {"run_progress": progress}),
                  patch.dict(os.environ, {}, clear=True),
                  patch.object(app.subprocess, "Popen", return_value=SimpleNamespace(pid=123)) as spawn):
                with self.assertRaisesRegex(ValueError, "密钥"):
                    app.remote_launch(payload)
                spawn.assert_not_called()
                os.environ["FLOW_STUDIO_TEST_ABS_KEY"] = "synthetic"
                self.assertEqual(app.remote_launch(payload)["output"], str(output))
                spawn.assert_called_once()
                self.assertIn("--abs", spawn.call_args.args[0])
                self.assertEqual(app.remote_launch(payload)["output"], str(output))
                spawn.assert_called_once()


class Handler(app.Handler):
    def log_message(self, *args):
        pass


@unittest.skipUnless(os.name == "posix", "The direct server accepts absolute POSIX paths")
class QueueHTTPTest(unittest.TestCase):
    @contextmanager
    def server(self, dataflow):
        with ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(self.dist))) as srv:
            self.origin = "http://127.0.0.1:" + str(srv.server_port)
            thread = threading.Thread(target=app.serve, args=(srv, dataflow))
            thread.start()
            try:
                yield
            finally:
                srv.shutdown()
                thread.join(4)
                self.assertFalse(thread.is_alive())

    def request(self, path, body=None):
        request = Request(self.origin + path,
                          data=json.dumps(body).encode() if body is not None else None,
                          headers={"Origin": self.origin, "Content-Type": "application/json"})
        try:
            response = urlopen(request, timeout=4)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def until(self, check):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if check():
                return
            time.sleep(.02)
        self.fail("队列状态未在预期时间内改变")

    def test_separate_submissions_share_one_worker(self):
        self.dist = app.ROOT / "dist"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            graph = {"id": "test", "version": 1,
                     "nodes": [{"id": n, "type": n} for n in ("input", "qwen", "segment", "output")],
                     "edges": [{"id": a, "from": a, "to": b} for a, b in
                               [("input", "qwen"), ("qwen", "segment"), ("segment", "output")]]}
            old = str(home / "already-running")
            current = {old: "running"}
            launches = []
            fingerprint = "0"*64
            dataset = {"selection": {"id": "take"}, "fingerprint": fingerprint, "sequenceName": "take"}
            ready = {"ready": True, "fingerprint": fingerprint, "sequenceName": "take", "issues": [], "runtimeIssues": []}

            def fake_remote(operation, payload=None, *, dataflow_root):
                if operation == "launch":
                    self.assertNotIn("running", current.values())
                    out = payload["out"]
                    launches.append(out)
                    current[out] = "running"
                    return {"output": out}
                return {"items": [{"output": out, "status": status} for out, status in current.copy().items()]}

            with (patch.object(Path, "home", return_value=home), patch.object(app, "ROOT", home),
                  patch.object(app, "inspect_dataset", return_value=ready),
                  patch.object(app, "prepare_dataset_launch", side_effect=lambda r, p: dict(p, input="/synthetic.jsonl")),
                  patch.object(app, "remote", side_effect=fake_remote),
                  patch.object(dataset_queue, "DatasetQueue", partial(dataset_queue.DatasetQueue, poll_interval=.04))):
                with self.server(home):
                    first, second = str(home / "take"), str(home / "manual")
                    body = {"graph": graph, "dataset": dataset, "out": first, "requestId": str(uuid4())}
                    self.assertEqual(self.request("/api/launch", body)[0], 202)
                    self.assertEqual(self.request("/api/launch", body)[0], 202)
                    self.assertEqual(self.request("/api/launch", {"graph": graph, "input": "/listed.jsonl", "out": second})[0], 202)
                    order = self.request("/api/queues")[1]["queueOrder"]
                    self.assertEqual(order, [first, second])
                    self.assertEqual(self.request("/api/queues/order", {"expected": order, "order": [second, first]})[0], 200)
                    self.assertEqual(self.request("/api/queues/order", {"expected": order, "order": order})[0], 409)
                    time.sleep(.12)
                    self.assertEqual(launches, [])
                    current[old] = "completed"
                    self.until(lambda: launches == [second])
                    waiting = self.request("/api/queues")[1]["queueOrder"]
                    self.assertEqual(waiting, [first])
                    self.assertEqual(self.request("/api/queues/order", {"expected": waiting, "order": [second]})[0], 400)
                    current[second] = "completed"
                    self.until(lambda: launches == [second, first])

    def test_named_presets_wait_for_explicit_start(self):
        self.dist = app.ROOT / "dist"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            order = ("input", "qwen", "segment", "output")
            graph = {"id": "test", "version": 2,
                     "nodes": [{"id": n, "type": n} for n in order],
                     "edges": [{"id": a, "from": a, "to": b} for a, b in zip(order, order[1:])]}
            fingerprint = "0"*64
            choice = {"selection": {"id": "take"}, "fingerprint": fingerprint, "sequenceName": "take"}
            ready = {"ready": True, "fingerprint": fingerprint, "sequenceName": "take", "issues": [], "runtimeIssues": []}
            with (patch.object(Path, "home", return_value=home), patch.object(app, "ROOT", home),
                  patch.object(app, "TASK_PRESETS", home / "presets.json"), patch.object(app, "LIBRARY", home/"library.json"),
                  patch.object(app, "inspect_dataset", return_value=ready),
                  patch.object(app, "remote", return_value={"items":[{"output": "/busy", "status": "running"}]}) as remote):
                with self.server(home):
                    for task, name in [("hands", "Hand examples"), ("objects", "Object examples")]:
                        graph["nodes"][0]["config"] = {"task": task}
                        status, result = self.request("/api/task-presets", {"name": name, "graph": graph, "datasets": [choice]})
                        self.assertEqual(status, 201, result)
                        self.assertEqual(result["preset"]["task"], task)
                    self.assertEqual(self.request("/api/queues")[1]["queueOrder"], [])
                    remote.assert_not_called()
                with self.server(home):
                    graph.update(version=3, name="New graph")
                    graph["nodes"][1]["name"] = "Latest Qwen"
                    self.assertEqual(self.request("/api/library", {"expectedRevision":0, "data":{
                        "graphs":[graph], "drafts":{}, "custom":[]}})[0], 200)
                    presets = self.request("/api/task-presets")[1]["presets"]
                    self.assertEqual([p["name"] for p in presets], ["Hand examples", "Object examples"])
                    body = {"id": presets[0]["id"], "requestId": str(uuid4())}
                    first = self.request("/api/task-presets/start", body)
                    second = self.request("/api/task-presets/start", body)
                    self.assertEqual(first[0], 202, first[1])
                    self.assertEqual(first[1]["queue"]["id"], second[1]["queue"]["id"])
                    self.assertEqual(first[1]["queue"]["task"], "hands")
                    self.assertEqual(len(self.request("/api/queues")[1]["queueOrder"]), 1)
                    stored = json.loads((home/".local/dataset-queues"/(body["requestId"]+".json")).read_text(encoding="utf-8"))
                    self.assertEqual(stored["graph"]["version"], 3)
                    self.assertEqual(stored["graph"]["nodes"][1]["name"], "Latest Qwen")

    def test_service_submission_recovery_and_failure(self):
        self.dist = app.ROOT / "dist"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account = root / "account"
            dataflow = account / "DataFlow"
            parent = dataflow / "dataflow_output" / "objects" / "new-batch"
            parent.parent.mkdir(parents=True)
            graph = {"id": "test-graph", "version": 1, "nodes": [], "edges": []}
            order = ["input", "qwen", "segment", "output"]
            for op in order:
                graph["nodes"].append({"id": op, "type": op, "name": op})
            graph["nodes"][0]["config"] = {"task": "objects"}
            for a, b in zip(order, order[1:]):
                graph["edges"].append({"id": a + "-" + b, "from": a, "to": b})
            choices = [{"selection": {"id": "video:" + name + "/rgb.mkv"}, "fingerprint": digit * 64,
                        "sequenceName": title}
                       for name, digit, title in [("take-a","a","kitchen_1"), ("take-b","b","kitchen_1"), ("take-c","c","take_c")]]
            body = {"requestId": str(uuid4()), "graph": graph, "out": str(parent), "datasets": choices}
            states = {}
            launches = []
            checked = []
            state_lock = threading.Lock()

            def inspect(dataflow, selection, graph):
                self.assertEqual(app.graph_task(graph), "objects")
                checked.append(selection["id"])
                choice = next(c for c in choices if c["selection"] == selection)
                return {"ready": True, "fingerprint": choice["fingerprint"], "sequenceName": choice["sequenceName"],
                        "issues": [], "runtimeIssues": []}

            def fake_remote(operation, payload=None, *, dataflow_root):
                with state_lock:
                    if operation == "launch":
                        self.assertEqual(app.graph_task(payload["graph"]), "objects")
                        out = payload["out"]
                        self.assertNotIn(out, launches)
                        Path(out).mkdir(parents=True)
                        launches.append(out)
                        states[out] = "running"
                        return {"output": out}
                    self.assertEqual(operation, "snapshots")
                    return {"items": [{"output": out, "status": status, "id": str(i), "started": 10, "rounds": []}
                                      for i, (out, status) in enumerate(states.items())], "otherRuns": 0}

            def queue_status():
                status, answer = self.request("/api/queues")
                self.assertEqual(status, 200)
                return answer["queues"][0]

            queue = partial(dataset_queue.DatasetQueue, poll_interval=.04)
            with (patch.object(Path, "home", return_value=account), patch.object(app, "ROOT", root),
                  patch.object(app, "inspect_dataset", side_effect=inspect),
                  patch.object(app, "prepare_dataset_launch", side_effect=lambda r, p: dict(p, input="/synthetic.jsonl")),
                  patch.object(app, "remote", side_effect=fake_remote), patch.object(dataset_queue, "DatasetQueue", queue)):
                self.assertEqual(app.next_sequence_output(parent.parent, "kitchen_1").name, "kitchen_1")
                occupied = parent.parent / "kitchen_1"
                occupied.mkdir()
                pending = parent.parent / "kitchen_1-02"
                receipt = sha256(str(pending).encode()).hexdigest()[:32]
                (account / ".local/state/dataflow-qwen-gencep/workbench" / receipt).mkdir(parents=True)
                self.assertEqual(app.next_sequence_output(parent.parent, "kitchen_1").name, "kitchen_1-03")
                with self.server(dataflow):
                    with urlopen(self.origin + "/", timeout=3) as webpage:
                        self.assertIn(b"studio.js", webpage.read())
                    self.assertEqual(self.request("/api/bridge")[1]["mode"], "direct")
                    bad = dict(body, datasets=[dict(choices[0], fingerprint="0"*64)])
                    self.assertEqual(self.request("/api/queues", bad)[0], 400)
                    bad_out = parent.parent.parent / "hands" / "incorrect-target"
                    self.assertEqual(self.request("/api/queues", dict(body, out=str(bad_out)))[0], 400)
                    bad_name = dict(body, datasets=[dict(choices[0], sequenceName="other_name")])
                    self.assertEqual(self.request("/api/queues", bad_name)[0], 400)
                    self.assertEqual(self.request("/api/queues")[1]["queues"], [])
                    status, answer = self.request("/api/queues", body)
                    self.assertEqual(status, 202, answer)
                    self.assertEqual(answer["queue"]["id"], body["requestId"])
                    self.assertEqual(answer["queue"]["task"], "objects")
                    self.assertEqual([Path(item["out"]).name for item in answer["queue"]["items"]],
                                     ["kitchen_1", "kitchen_1-02", "take_c"])
                    self.until(lambda: queue_status()["items"][0]["status"] == "running")
                    first = launches[0]
                    self.assertTrue(parent.exists())
                    self.assertEqual(len(launches), 1)
                    status, live = self.request("/api/live")
                    self.assertEqual(status, 200)
                    self.assertEqual(live["queues"][0]["id"], body["requestId"])
                    self.assertEqual(live["runs"][0]["output"], first)

                with self.server(dataflow):
                    before = checked[:]
                    self.assertEqual(self.request("/api/queues", body)[0], 202)
                    self.assertEqual(checked, before)
                    self.assertEqual(len(launches), 1)
                    with state_lock:
                        states[first] = "done"
                    self.until(lambda: queue_status()["items"][1]["status"] == "running")
                    with state_lock:
                        states[launches[1]] = "failed"
                    self.until(lambda: queue_status()["status"] == "paused")
                    final = self.request("/api/live")[1]["queues"][0]
                    self.assertEqual([item["status"] for item in final["items"]], ["completed","failed","paused"])
                    self.assertEqual(len(launches), 2)
                    self.assertEqual(self.request("/api/queues", dict(body, datasets=choices[:1]))[0], 400)
                    self.assertEqual(queue_status()["status"], "paused")


if __name__ == "__main__":
    unittest.main()
