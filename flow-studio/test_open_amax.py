import unittest
from unittest.mock import Mock, patch

import open_amax


class AmaxLauncherTest(unittest.TestCase):
    def test_personal_alias_forwards_to_its_own_socket_without_owner_relay(self):
        profile = {"hostname": "amax.example", "user": "collaborator"}
        process = Mock()
        process.poll.return_value = None
        with (
            patch("sys.argv", ["open_amax.py", "--no-browser"]),
            patch.object(open_amax, "ssh_profile", return_value=profile),
            patch.object(open_amax, "workbench_info", side_effect=[{}, {"connected": True}]),
            patch.object(open_amax.subprocess, "Popen", return_value=process) as spawn,
            patch.object(open_amax.time, "sleep", side_effect=KeyboardInterrupt),
        ):
            self.assertEqual(open_amax.main(), 0)
        spawn.assert_called_once()
        command = spawn.call_args.args[0]
        self.assertIn("127.0.0.1:18766:/home/collaborator/DataFlow/flow-studio/.local/workbench.sock", command)
        self.assertIn("amax-workbench", command)
        self.assertNotIn("-R", command)
        process.terminate.assert_called_once()
