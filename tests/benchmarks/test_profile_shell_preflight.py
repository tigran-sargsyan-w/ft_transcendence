"""Offline tests: no Docker, Vault, network, or repository mutation."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

MODULE = Path(__file__).with_name("profile_shell_preflight.py")
spec = importlib.util.spec_from_file_location("profile_shell_preflight", MODULE)
prof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prof)


class TraceTests(unittest.TestCase):
    def test_wrapper_never_includes_arguments_or_secrets(self):
        script = prof.wrapper_source("docker", Path("/bin/echo"), Path("/tmp/trace"))
        self.assertIn('"$@"', script)
        self.assertNotIn('set -x', script)
        self.assertNotIn('$*', script)
        self.assertNotIn('env ', script)

    def test_wrapper_preserves_status_and_argv_without_logging(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "trace"
            script = Path(temp) / "shell"
            prog = Path(temp) / "tool"
            prog.write_text('#!/bin/sh\n[ "$1" = "SECRET-CANARY" ] && exit 7\n')
            prog.chmod(0o700)
            script.write_text(prof.wrapper_source("docker", prog, log))
            script.chmod(0o700)
            result = subprocess.run([str(script), "SECRET-CANARY"], capture_output=True)
            self.assertEqual(result.returncode, 7)
            self.assertNotIn("SECRET-CANARY", log.read_text())
            calls, pending = prof.parse_records(log.read_text())
            self.assertEqual(pending, 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1:], ("docker.other", 7))

    def test_docker_classifications_and_unmodified_args(self):
        cases = (
            (("compose", "--profile", "setup", "vault-bootstrap"), "docker.compose.vault-config"),
            (("compose", "config", "--services"), "docker.compose.other"),
            (("run", "--env", "SECRET-CANARY", "/bootstrap/helper.py", "check"), "docker.run.vault-secrets"),
            (("run", "--rm", "SECRET-CANARY"), "docker.run.other"),
            (("exec", "transcendence-app-dev-postgres-1", "true"), "docker.exec.postgres"),
            (("exec", "transcendence-app-dev-nginx-1", "true"), "docker.exec.nginx"),
            (("exec", "SECRET-CANARY", "true"), "docker.exec.other"),
            (("buildx", "version"), "docker.buildx"),
            (("volume", "inspect", "SECRET-CANARY"), "docker.volume"),
        )
        for arguments, expected in cases:
            with self.subTest(category=expected), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                log = base / "trace"
                executable = base / "docker-cli"
                wrapper = base / "docker"
                executable.write_text('#!/bin/sh\n[ "$1" != "SECRET-CANARY" ]\n')
                executable.chmod(0o700)
                wrapper.write_text(prof.wrapper_source("docker", executable, log))
                wrapper.chmod(0o700)
                result = subprocess.run([str(wrapper), *arguments], capture_output=True)
                self.assertEqual(result.returncode, 0)
                self.assertNotIn("SECRET-CANARY", log.read_text())
                records, pending = prof.parse_records(log.read_text())
                self.assertEqual(pending, 0)
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0][1], expected)

    def test_parser_rejects_unknown_docker_label(self):
        records, pending = prof.parse_records(
            "B\tdocker.run.SECRET-CANARY\t8\t100\n"
            "E\tdocker.run.SECRET-CANARY\t8\t200\t0\n"
        )
        self.assertEqual(records, [])
        self.assertEqual(pending, 0)

    def test_parser_handles_incomplete_calls(self):
        calls, pending = prof.parse_records("B\tdocker\t8\t100\nE\tdocker\t8\t200\t0\nB\tpython3\t9\t100\n")
        self.assertEqual(len(calls), 1)
        self.assertEqual(pending, 1)

    def test_parser_ignores_untrusted_text(self):
        calls, pending = prof.parse_records("B\tsecret-token\t8\t100\n" +
            "E\tsecret-token\t8\t200\t0\nrandom login token=SECRET\n")
        self.assertFalse(calls)
        self.assertEqual(pending, 0)

    def test_parser_skips_bad_numbers(self):
        calls, pending = prof.parse_records("B\tdocker\tfoo\t1\nE\tdocker\tfoo\t2\t0\n")
        self.assertFalse(calls)
        self.assertEqual(pending, 0)


if __name__ == "__main__":
    unittest.main()
