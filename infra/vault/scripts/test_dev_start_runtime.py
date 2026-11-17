import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

module = Path(__file__).with_name("dev-start-runtime.py")
spec = importlib.util.spec_from_file_location("startup", module)
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


class StartupTests(unittest.TestCase):

    @patch.object(startup, "wait_ready")
    @patch.object(startup, "execute")
    @patch.object(startup, "inspect")
    def test_running_container_not_recreated(
        self, inspect, execute, wait
    ):
        inspect.return_value = {
            "State": {"Status": "running"}
        }

        startup.ensure_running(startup.APP, "postgres")

        execute.assert_not_called()
        wait.assert_called_once()

    @patch.object(startup, "wait_ready")
    @patch.object(startup, "execute")
    @patch.object(startup, "inspect")
    def test_stopped_container_only_started(
        self, inspect, execute, wait
    ):
        inspect.return_value = {
            "State": {"Status": "exited"}
        }

        startup.ensure_running(startup.APP, "postgres")

        execute.assert_called_once_with(
            [
                "docker", "start",
                "transcendence-app-dev-postgres-1",
            ],
            timeout=60,
        )
        wait.assert_called_once()

    @patch.object(startup, "execute")
    @patch.object(startup, "inspect")
    def test_paused_container_rejected(
        self, inspect, execute
    ):
        inspect.return_value = {
            "State": {"Status": "paused"}
        }

        with self.assertRaises(startup.StartupError):
            startup.ensure_running(startup.APP, "backend")

        execute.assert_not_called()

    @patch.object(startup, "docker_json")
    def test_legacy_compose_rejected(self, docker_json):
        docker_json.return_value = [{
            "Config": {
                "Labels": {
                    "com.docker.compose.project": startup.APP,
                    "com.docker.compose.service": "backend",
                    "com.docker.compose.project.config_files":
                        "/tmp/legacy.yml",
                },
            },
        }]

        with self.assertRaisesRegex(
            startup.StartupError, "not unified"
        ):
            startup.inspect(startup.APP, "backend")

    @patch.object(startup, "vault_status")
    def test_uninitialized_vault_rejected(self, status):
        status.return_value = {
            "initialized": False,
            "sealed": True,
            "n": 3,
            "t": 2,
        }

        with self.assertRaisesRegex(
            startup.StartupError, "not initialized"
        ):
            startup.verify_vault()

    @patch.object(startup, "vault_status")
    def test_partial_unseal_rejected(self, status):
        status.return_value = {
            "initialized": True,
            "sealed": True,
            "n": 3,
            "t": 2,
            "progress": 1,
        }

        with self.assertRaisesRegex(
            startup.StartupError, "Partial unseal"
        ):
            startup.verify_vault()

    @patch.object(startup, "vault_status")
    def test_unsealed_vault_needs_no_keys(self, status):
        status.return_value = {
            "initialized": True,
            "sealed": False,
            "n": 3,
            "t": 2,
            "seal_type": "shamir",
            "storage_type": "raft",
        }

        startup.verify_vault()

    @patch.object(startup, "execute")
    @patch.object(startup, "vault_status")
    @patch.object(startup.getpass, "getpass")
    @patch.object(
        startup.sys.stderr, "isatty", return_value=True
    )
    def test_unseal_keys_passed_only_via_stdin(
        self, tty, getpass, status, execute
    ):
        status.side_effect = [
            {
                "initialized": True,
                "sealed": True,
                "n": 3,
                "t": 2,
                "progress": 0,
            },
            {
                "initialized": True,
                "sealed": True,
                "n": 3,
                "t": 2,
                "progress": 1,
            },
            {
                "initialized": True,
                "sealed": False,
                "n": 3,
                "t": 2,
                "storage_type": "raft",
            },
            {
                "initialized": True,
                "sealed": False,
                "n": 3,
                "t": 2,
                "storage_type": "raft",
            },
        ]

        getpass.side_effect = ["secret-one", "secret-two"]

        startup.verify_vault()

        self.assertEqual(execute.call_count, 2)

        for call in execute.call_args_list:
            args = call.args[0]
            self.assertEqual(args[:3], ["docker", "exec", "-i"])
            self.assertNotIn("secret-one", repr(args))
            self.assertNotIn("secret-two", repr(args))


if __name__ == "__main__":
    unittest.main()
