"""On amax: python3 -B test_operator_agent.py. Uses a synthetic loopback provider."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from operator_agent import design, status


ROOT = Path("/data/yanghonglei/DataFlow")


@unittest.skipUnless(status(ROOT)["installed"], "run where the private DataFlow Writer is installed")
class WriterIntegration(unittest.TestCase):
    def test_selected_source_and_requirement_go_to_the_native_writer(self):
        pairs = [
            ("output", "native", "def workbench_output(result):\n    print(result)"),
            ("python", "records", "from dataflow.core import OperatorABC\nclass Copy(OperatorABC):\n"
             "    def run(self, storage):\n        storage.write(storage.read('dict'))"),
        ]
        requests = []
        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if body["model"] == "fixture-denied":
                    self.send_error(401, "private-fixture-error")
                    return
                requests.append((self.path, body, self.headers.get("Authorization")))
                source = pairs[min(len(requests), len(pairs)) - 1][2] + "\n# draft"
                reply = json.dumps({"model": "local-fixture", "id": "fixture", "created": 0,
                                    "object": "chat.completion", "choices": [{"index": 0,
                                    "finish_reason": "stop", "message": {"role": "assistant",
                                    "content": json.dumps({"code": source, "desc": "example"})}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

        with HTTPServer(("127.0.0.1", 0), Provider) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = {"FLOW_STUDIO_AGENT_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                       "FLOW_STUDIO_AGENT_MODEL": "local-fixture", "FLOW_STUDIO_AGENT_API_KEY": "",
                       "OPENAI_BASE_URL": "http://127.0.0.1:1/v1", "OPENAI_API_KEY": "unused-fixture"}
                with patch.dict(os.environ, env):
                    self.assertTrue(status(ROOT)["ready"])
                    for op_type, kind, source in pairs:
                        requirement = "Keep field IDs in the " + kind + " implementation"
                        implementation = {"kind": kind, "code": source, "baseHash": "0" * 64}
                        result = design(ROOT, op_type, implementation, requirement)
                        self.assertEqual(result["code"], source + "\n# draft")
                        path, request, _ = requests[-1]
                        self.assertEqual(path, "/v1/chat/completions")
                        self.assertEqual(request["model"], "local-fixture")
                        self.assertFalse(request.get("tools"))
                        system, user = [message["content"] for message in request["messages"]]
                        self.assertIn("data operator development expert", system)
                        self.assertIn(requirement, user)
                        self.assertIn(source, user)
                    os.environ["FLOW_STUDIO_AGENT_MODEL"] = "fixture-denied"
                    with self.assertRaisesRegex(ConnectionError, "HTTP 401") as error:
                        design(ROOT, op_type, implementation, requirement)
                    self.assertNotIn("private-fixture-error", str(error.exception))
                # Reuse ABS with a synthetic config and credential only. The actual
                # model is taken from the same pipeline literal passed to local_score.
                with TemporaryDirectory() as home:
                    config = Path(home) / "scoring_scripts/abs/api_config.json"
                    config.parent.mkdir(parents=True)
                    api = {"base_url": f"http://127.0.0.1:{server.server_port}/v1",
                           "key_env": "SYNTHETIC_ABS_KEY", "timeout_seconds": 20, "parameters": {}}
                    config.write_text(json.dumps({"api": api}), encoding="utf-8")
                    env = {"HOME": home, "FLOW_STUDIO_AGENT_BASE_URL": "", "FLOW_STUDIO_AGENT_MODEL": "",
                           "FLOW_STUDIO_AGENT_API_KEY": "", "SYNTHETIC_ABS_KEY": ""}
                    with patch.dict(os.environ, env):
                        info = status(ROOT)
                        self.assertEqual((info["source"], info["ready"], info["key_env"]),
                                         ("abs", False, "SYNTHETIC_ABS_KEY"))
                        self.assertTrue(info["model"])
                        os.environ["SYNTHETIC_ABS_KEY"] = "fixture-only"
                        self.assertTrue(status(ROOT)["ready"])
                        for parameters in ({}, {"reasoning_effort": "low", "extra_body": {"thinking": {"type": "enabled"}}}):
                            api["parameters"] = parameters
                            config.write_text(json.dumps({"api": api}), encoding="utf-8")
                            answer = design(ROOT, op_type, implementation, requirement)
                            self.assertEqual(answer["code"], source + "\n# draft")
                            path, request, header = requests[-1]
                            self.assertEqual(path, "/v1/chat/completions")
                            self.assertEqual(header, "Bearer fixture-only")
                            self.assertEqual(request["model"], info["model"])
                            self.assertNotIn("temperature", request)
                            self.assertEqual(request.get("reasoning_effort"), parameters.get("reasoning_effort"))
                            self.assertEqual(request.get("thinking"), parameters.get("extra_body", {}).get("thinking"))
                        os.environ.update(FLOW_STUDIO_AGENT_BASE_URL="https://override.invalid/v1",
                                          FLOW_STUDIO_AGENT_MODEL="override")
                        info = status(ROOT)
                        self.assertEqual((info["source"], info["ready"], info["key_env"]),
                                         ("custom", False, "FLOW_STUDIO_AGENT_API_KEY"))
                self.assertEqual(len(requests), 4)
            finally:
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    unittest.main()
