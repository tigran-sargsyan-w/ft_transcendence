"""Offline tests for development Vault unseal environment checks."""

import importlib.util
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("dev-unseal.py")
spec = importlib.util.spec_from_file_location("dev_unseal", SCRIPT)
dev_unseal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev_unseal)


def result(output="", returncode=0):
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=output,
        stderr="",
    )


class DoctorTests(unittest.TestCase):
    def run_doctor(
        self,
        alias='o "/org/freedesktop/secrets/collection/login"',
        locked="b false",
        missing=(),
        docker_available=True,
        service_available=True,
        ca_available=True,
    ):
        calls = []

        def fake_which(name):
            return None if name in missing else f"/usr/bin/{name}"

        def fake_probe(command, timeout=8):
            calls.append(command)

            if command[:2] == ["docker", "compose"]:
                return result()

            if command[:2] == ["docker", "info"]:
                return result(
                    returncode=0 if docker_available else 1
                )

            if "ReadAlias" in command:
                return result(
                    alias,
                    returncode=0 if service_available else 1,
                )

            if "Locked" in command:
                return result(locked)

            raise AssertionError(f"Unexpected command: {command}")

        with tempfile.TemporaryDirectory() as directory:
            tls_dir = Path(directory)

            if ca_available:
                (tls_dir / "ca.crt").write_text(
                    "test certificate placeholder"
                )

            output = io.StringIO()

            with (
                patch.object(
                    dev_unseal.shutil,
                    "which",
                    side_effect=fake_which,
                ),
                patch.object(
                    dev_unseal,
                    "probe",
                    side_effect=fake_probe,
                ),
                redirect_stdout(output),
            ):
                status = dev_unseal.doctor(tls_dir)

        return status, output.getvalue(), calls

    def test_ready_environment(self):
        status, output, _ = self.run_doctor()

        self.assertEqual(status, 0)
        self.assertIn("Default Keyring: unlocked", output)
        self.assertIn("RESULT: CHECKS COMPLETE", output)

    def test_locked_keyring_is_warning(self):
        status, output, _ = self.run_doctor(locked="b true")

        self.assertEqual(status, 0)
        self.assertIn("Default Keyring: locked", output)

    def test_missing_default_keyring(self):
        status, output, _ = self.run_doctor(alias='o "/"')

        self.assertEqual(status, 0)
        self.assertIn("not configured", output)

    def test_temporary_collection_is_not_accepted(self):
        status, output, _ = self.run_doctor(
            alias='o "/org/freedesktop/secrets/collection/session"'
        )

        self.assertEqual(status, 0)
        self.assertIn("temporary session collection", output)
        self.assertNotIn("Default Keyring: unlocked", output)

    def test_missing_dependency_fails(self):
        status, output, _ = self.run_doctor(
            missing=("secret-tool",)
        )

        self.assertEqual(status, 1)
        self.assertIn("secret-tool: missing", output)
        self.assertIn("RESULT: NOT READY", output)

    def test_unavailable_docker_fails(self):
        status, output, _ = self.run_doctor(
            docker_available=False
        )

        self.assertEqual(status, 1)
        self.assertIn("Docker Engine: unavailable", output)

    def test_unavailable_secret_service_fails(self):
        status, output, _ = self.run_doctor(
            service_available=False
        )

        self.assertEqual(status, 1)
        self.assertIn("Secret Service: unavailable", output)

    def test_missing_ca_is_warning(self):
        status, output, _ = self.run_doctor(
            ca_available=False
        )

        self.assertEqual(status, 0)
        self.assertIn("Vault CA: not configured", output)

    def test_invalid_lock_status_fails(self):
        status, output, _ = self.run_doctor(
            locked="unexpected"
        )

        self.assertEqual(status, 1)
        self.assertIn("invalid lock status", output)

    def test_doctor_does_not_modify_environment(self):
        _, _, calls = self.run_doctor()

        for command in calls:
            self.assertNotIn("start", command)
            self.assertNotIn("unseal", command)
            self.assertNotIn("store", command)
            self.assertNotIn("exec", command)


if __name__ == "__main__":
    unittest.main()
