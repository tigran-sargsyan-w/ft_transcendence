"""Offline safety tests: intercept Docker CLI so no real Docker changes occur."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("demo.sh")


class CommandSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.docker_log = root / "docker.log"
        docker = root / "docker"
        docker.write_text("""#!/bin/sh
printf '%s\\n' \"$*\" >> \"$DEMO_TEST_LOG\"
case \"$*\" in
  *\"ps --status running --services\"*) printf 'db\\napi\\nweb\\nworker\\n' ;;
  *\"ps -q --all\"*) printf 'lab-only-test-container-id\\n' ;;
  *\"com.docker.compose.depends_on\"*)
    if [ "${DEMO_TEST_MISSING_DEPENDENCY:-0}" = 1 ]; then
      printf '\n'
    else
      printf 'api:service_healthy:false\n'
    fi
    ;;
  *\"inspect --format\"*) printf 'healthy\\n' ;;
esac
""")
        docker.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{root}:{os.environ.get('PATH', '')}", DEMO_TEST_LOG=str(self.docker_log))

    def tearDown(self):
        self.tempdir.cleanup()

    def run_demo(self, *args):
        return subprocess.run(
            ["bash", str(SCRIPT), *args], text=True, capture_output=True, env=self.env, check=False,
        )

    def commands(self):
        return self.docker_log.read_text().splitlines() if self.docker_log.exists() else []

    def assert_lab_scoped(self):
        for command in self.commands():
            if command.startswith("compose "):
                self.assertIn("--project-name ft-demo-lab", command)
                self.assertIn("/demo-lab/compose.yml", command)
            else:
                self.assertTrue(command.startswith("inspect --format"), command)
        self.assertNotIn("--volumes", " ".join(self.commands()))
        self.assertNotIn("system prune", " ".join(self.commands()))

    def test_invalid_service_never_calls_docker(self):
        for args in [("stop", "backend"), ("recreate", "vault"), ("fail", "postgres"),
                     ("start", "nginx"), ("add", "backend"), ("remove", "api")]:
            self.docker_log.unlink(missing_ok=True)
            result = self.run_demo(*args)
            self.assertNotEqual(result.returncode, 0, args)
            self.assertEqual(self.commands(), [], args)

    def test_up_is_baseline_only(self):
        result = self.run_demo("up")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.commands()), 1)
        self.assertIn("up -d --build --wait --wait-timeout 120", self.commands()[0])
        self.assertNotIn("--profile discovery", self.commands()[0])
        self.assert_lab_scoped()

    def test_stop_starts_only_named_lab_service(self):
        for action in ("stop", "start"):
            self.docker_log.unlink(missing_ok=True)
            result = self.run_demo(action, "api")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(any(command.endswith(f"{action} api") for command in self.commands()))
            self.assert_lab_scoped()

    def test_recreate_is_single_service_without_recreating_dependencies(self):
        result = self.run_demo("recreate", "api")
        self.assertEqual(result.returncode, 0, result.stderr)
        cmds = self.commands()
        self.assertTrue(any(command.endswith("rm --stop --force api") for command in cmds))
        self.assertTrue(any("up -d --no-recreate --wait --wait-timeout 120 api" in command
                            for command in cmds))
        self.assertFalse(any("--no-deps" in command or "--force-recreate" in command
                             for command in cmds))
        self.assert_lab_scoped()

    def test_add_worker_preserves_dependency_label(self):
        result = self.run_demo("add", "worker")
        self.assertEqual(result.returncode, 0, result.stderr)
        cmds = self.commands()
        self.assertTrue(any("--profile discovery up -d --no-recreate" in command
                            and command.endswith("worker") for command in cmds))
        self.assertFalse(any("--no-deps" in command for command in cmds))
        self.assertTrue(any("com.docker.compose.depends_on" in command for command in cmds))
        self.assert_lab_scoped()

    def test_add_worker_rejects_missing_dependency_label(self):
        self.env["DEMO_TEST_MISSING_DEPENDENCY"] = "1"
        result = self.run_demo("add", "worker")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("without the Compose api dependency label", result.stderr)
        self.assert_lab_scoped()

    def test_remove_worker_is_targeted(self):
        result = self.run_demo("remove", "worker")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.commands()), 1)
        self.assertTrue(self.commands()[0].endswith("rm --stop --force worker"))
        self.assert_lab_scoped()

    def test_down_cleans_only_lab_project(self):
        result = self.run_demo("down")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.commands()), 1)
        self.assertTrue(self.commands()[0].endswith("--profile discovery down"))
        self.assert_lab_scoped()

    def test_reset_restores_baseline_without_unsafe_cleanup(self):
        result = self.run_demo("reset")
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.commands()
        self.assertTrue(any(command.endswith("rm --stop --force worker") for command in commands))
        self.assertTrue(any(command.endswith("start db api web") for command in commands))
        for service in ("db", "api", "web"):
            self.assertTrue(any(f"exec -T {service} sh -c rm -f /run/demo/fail" in command for command in commands))
        self.assert_lab_scoped()


if __name__ == "__main__":
    unittest.main()
