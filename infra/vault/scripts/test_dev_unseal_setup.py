"""Offline tests for Issue #107 Keyring setup."""

import importlib.util
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("dev-unseal.py")
spec = importlib.util.spec_from_file_location("dev_unseal_setup", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def result(stdout="", code=0):
    return subprocess.CompletedProcess(
        args=[], returncode=code, stdout=stdout, stderr=""
    )


class SetupTests(unittest.TestCase):
    def exercise(
        self,
        alias="login",
        locked=True,
        answer="y",
        passwords=("example-test-password",),
        service=True,
        daemon_code=0,
        file_mode=0o600,
    ):
        calls = []
        state = {"replaced": False}

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            keyring_dir = home / ".local/share/keyrings"
            keyring_dir.mkdir(parents=True)
            keyring_dir.chmod(0o700)

            keyring_file = keyring_dir / "login.keyring"
            keyring_file.write_text("offline test fixture")
            keyring_file.chmod(file_mode)

            def fake_probe(command, timeout=8):
                if "status" in command:
                    return result(
                        "gnome-keyring-daemon" if service else "",
                        0 if service else 1,
                    )

                if "ReadAlias" in command:
                    name = "login" if state["replaced"] else alias
                    path = (
                        "/" if name == "/"
                        else f"/org/freedesktop/secrets/collection/{name}"
                    )
                    return result(f'o "{path}"')

                if "Locked" in command:
                    is_locked = locked and not state["replaced"]
                    return result("b true" if is_locked else "b false")

                raise AssertionError(f"Unexpected probe: {command}")

            def fake_run(command, **kwargs):
                calls.append((command, kwargs))
                if daemon_code == 0:
                    state["replaced"] = True
                return result(code=daemon_code)

            output = io.StringIO()

            with (
                patch.object(module.shutil, "which",
                             side_effect=lambda name: f"/usr/bin/{name}"),
                patch.object(module, "probe", side_effect=fake_probe),
                patch.object(module.subprocess, "run",
                             side_effect=fake_run),
                patch.object(module.Path, "home", return_value=home),
                patch("builtins.input", return_value=answer),
                patch.object(module.getpass, "getpass",
                             side_effect=list(passwords)) as prompt,
                redirect_stdout(output),
            ):
                status = module.setup_keyring()
                prompt_count = prompt.call_count

        return status, output.getvalue(), calls, prompt_count

    def test_existing_unlocked_keyring_is_noop(self):
        status, output, calls, prompts = self.exercise(locked=False)
        self.assertEqual(status, 0)
        self.assertIn("already unlocked", output)
        self.assertEqual(calls, [])
        self.assertEqual(prompts, 0)

    def test_locked_keyring_unlocks(self):
        status, output, calls, prompts = self.exercise()
        self.assertEqual(status, 0)
        self.assertIn("configured and unlocked", output)
        self.assertEqual(len(calls), 1)
        self.assertEqual(prompts, 1)
        self.assertEqual(calls[0][1]["input"], "example-test-password")
        self.assertNotIn("example-test-password", output)
        self.assertNotIn("example-test-password", calls[0][0])

    def test_missing_default_creates_login(self):
        status, output, calls, prompts = self.exercise(
            alias="/",
            passwords=("example-password", "example-password"),
        )
        self.assertEqual(status, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(prompts, 2)
        self.assertIn("configured and unlocked", output)

    def test_session_collection_creates_login(self):
        status, _, calls, _ = self.exercise(
            alias="session",
            passwords=("example-password", "example-password"),
        )
        self.assertEqual(status, 0)
        self.assertEqual(len(calls), 1)

    def test_declined_setup_does_nothing(self):
        status, output, calls, prompts = self.exercise(answer="n")
        self.assertEqual(status, 1)
        self.assertIn("cancelled", output)
        self.assertEqual(calls, [])
        self.assertEqual(prompts, 0)

    def test_empty_password_rejected(self):
        status, output, calls, _ = self.exercise(passwords=("",))
        self.assertEqual(status, 1)
        self.assertIn("Empty Keyring password", output)
        self.assertEqual(calls, [])

    def test_password_confirmation_mismatch(self):
        status, output, calls, _ = self.exercise(
            alias="/",
            passwords=("password-one", "password-two"),
        )
        self.assertEqual(status, 1)
        self.assertIn("Passwords do not match", output)
        self.assertEqual(calls, [])

    def test_unexpected_collection_rejected(self):
        status, output, calls, _ = self.exercise(alias="custom")
        self.assertEqual(status, 1)
        self.assertIn("Unexpected default collection", output)
        self.assertEqual(calls, [])

    def test_unknown_secret_service_provider_rejected(self):
        status, output, calls, _ = self.exercise(service=False)
        self.assertEqual(status, 1)
        self.assertIn("provider not confirmed", output)
        self.assertEqual(calls, [])

    def test_daemon_failure_is_reported(self):
        status, output, calls, _ = self.exercise(daemon_code=1)
        self.assertEqual(status, 1)
        self.assertIn("could not be initialized", output)
        self.assertEqual(len(calls), 1)

    def test_unsafe_permissions_rejected(self):
        status, output, _, _ = self.exercise(file_mode=0o644)
        self.assertEqual(status, 1)
        self.assertIn("unsafe permissions", output)


if __name__ == "__main__":
    unittest.main()
