import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from abs_transport import proxy_environment, route_scorer


class AbsTransportTest(unittest.TestCase):
    def test_only_the_configured_abs_origin_and_runner_use_the_relay(self):
        origin = "http://abs.test:12333"
        proxy = "http://127.0.0.1:18767"
        with patch.dict(os.environ, {"FLOW_STUDIO_ABS_HTTP_PROXY": proxy,
                                     "FLOW_STUDIO_ABS_PROXY_ORIGIN": origin}):
            self.assertEqual(proxy_environment(origin + "/v1")["http_proxy"], proxy)
            self.assertFalse(proxy_environment("http://other.test/v1"))
            self.assertFalse(proxy_environment("https://abs.test:12333/v1"))
            with TemporaryDirectory() as tmp:
                config = Path(tmp) / "config.json"
                config.write_text(json.dumps({"api": {"base_url": origin + "/v1"}}))
                logger = Mock()
                scored = route_scorer(logger, Path("/example/local_score.py"))
                command = ["python", "-B", "/example/local_score.py", "--config", str(config)]
                for extra in ([], ["--resume"]):
                    scored([*command, *extra], "abs.log", env={})
                    self.assertEqual(logger.call_args.kwargs["env"]["HTTP_PROXY"], proxy)
                scored([*command, "--prepare-only"], "abs.log", env={})
                self.assertEqual(logger.call_args.kwargs["env"], {})
                scored(["python", "-B", "/example/experiment.py", "--config", str(config)], "qwen.log")
                self.assertIsNone(logger.call_args.kwargs["env"])
