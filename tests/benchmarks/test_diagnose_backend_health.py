"""Offline tests: diagnostic must not mutate Docker or disclose command output."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

path = Path(__file__).with_name("diagnose_backend_health.py")
spec = importlib.util.spec_from_file_location("diagnose_backend_health", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class DiagnosisTests(unittest.TestCase):
    def test_commands_are_fixed_read_only(self):
        self.assertEqual(len(mod.CASES), 3)
        self.assertEqual(mod.CASES[0][1][1], "-q")
        for name, command in mod.CASES:
            self.assertNotIn("-k", command)
            self.assertNotIn("--insecure", command)
            self.assertNotIn("--privileged", command)
            if command[0] == "docker":
                self.assertEqual(command[1], "exec")
                self.assertEqual(command[2], mod.NAME)
                self.assertNotIn("token", " ".join(command).lower())

    def test_http_status_is_checked_without_body(self):
        with patch.object(mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, stdout=b"503", stderr=b"")
            _, status = mod.probe("https-nginx", mod.CASES[0][1], 12)
        self.assertEqual(status, "HTTP_NOT_200")

    def test_success(self):
        with patch.object(mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, stdout=None, stderr=None)
            _, status = mod.probe("docker-exec-node", mod.CASES[1][1], 12)
        self.assertEqual(status, "PASS")

    def test_timeout_is_not_swallowed(self):
        with patch.object(mod.subprocess, "run", side_effect=subprocess.TimeoutExpired(["docker"], 12)):
            _, status = mod.probe("docker-exec-health", mod.CASES[2][1], 12)
        self.assertEqual(status, "TIMEOUT")

    def test_backend_status_only(self):
        with patch.object(mod.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, stdout=b"true healthy\n", stderr=b"")
            self.assertTrue(mod.inspect_backend(12))
            argv = run.call_args.args[0]
            self.assertEqual(argv[:2], ["docker", "inspect"])
            self.assertNotIn("--format=json", argv)

    def test_preflight_failure_stops_without_probes(self):
        with patch.object(mod, "inspect_backend", return_value=False), \
             patch.object(mod, "probe") as probe, \
             patch.object(mod.Path, "is_file", return_value=True):
            status = mod.main(["--runs", "1"])
        self.assertEqual(status, 1)
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
