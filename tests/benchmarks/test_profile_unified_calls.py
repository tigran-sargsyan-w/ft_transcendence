"""Offline tests of sanitized per-probe timing labels; no Docker required."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("profile_unified_calls", HERE / "profile_unified_calls.py")
profiler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiler)


class ProfileTests(unittest.TestCase):
    def test_backend_agent_mode_is_read_only(self):
        self.assertEqual(
            profiler.COMMANDS["backend-agent"],
            ["./scripts/bootstrap.sh", "--check-backend-agent"],
        )

    def test_known_probe_labels_do_not_leak_arguments(self):
        with tempfile.TemporaryDirectory(prefix="ft-profile-test-") as temp:
            base = Path(temp)
            # Emulate two original call sites, and an unrelated docker run.
            (base / "bootstrap_backend_agent.py").write_text(r"""import subprocess

def run(argv):
    return subprocess.run(argv, check=True)

def credential_inventory():
    run(["docker", "run", "SECRET-MUST-NOT-LOG"])

def verify_rendered_access():
    run(["docker", "run", "SECRET-MUST-NOT-LOG"])

def unrelated():
    run(["docker", "run", "SECRET-MUST-NOT-LOG"])
""")
            (base / "sitecustomize.py").write_text(profiler.TRACE)
            docker = base / "docker"
            docker.write_text("#!/bin/sh\nexit 0\n")
            docker.chmod(0o700)
            log = base / "records.jsonl"
            log.touch(mode=0o600)
            env = os.environ.copy()
            env.update({
                "PATH": str(base) + os.pathsep + env.get("PATH", ""),
                "PYTHONPATH": str(base),
                "FT_PROFILE_LOG": str(log),
                "PYTHONDONTWRITEBYTECODE": "1",
            })
            python = "/usr/bin/python3" if Path("/usr/bin/python3").is_file() else sys.executable
            cmd = [python, "-c", "import bootstrap_backend_agent as b; "
                   "b.credential_inventory(); b.verify_rendered_access(); b.unrelated()"]
            result = subprocess.run(cmd, env=env, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            raw = log.read_text()
            self.assertNotIn("SECRET-MUST-NOT-LOG", raw)
            labels = [json.loads(line)["kind"] for line in raw.splitlines()]
            self.assertEqual(labels, [
                "docker run/auth-probe", "docker run/DSN-probe", "docker run",
            ])

    def test_uninstrumented_control_path_unchanged(self):
        self.assertEqual(profiler.self_test(), 0)


if __name__ == "__main__":
    unittest.main()
