import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

path = Path(__file__).with_name("dev-start-runtime.py")
spec = importlib.util.spec_from_file_location("runtime_http", path)
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


class HTTPReadinessTests(unittest.TestCase):

    @patch.object(startup.time, "sleep")
    @patch.object(startup, "execute")
    def test_frontend_becomes_ready(self, execute, sleep):
        execute.side_effect = [
            startup.StartupError("not ready"),
            b"",
        ]

        startup.wait_frontend_ready(attempts=3, delay=0)

        self.assertEqual(execute.call_count, 2)

    @patch.object(startup.time, "sleep")
    @patch.object(startup, "execute")
    def test_nginx_recovers_after_502(self, execute, sleep):
        execute.side_effect = [b"502", b"200"]

        startup.wait_https_route("/", attempts=3, delay=0)

        self.assertEqual(execute.call_count, 2)

    @patch.object(startup.time, "sleep")
    @patch.object(startup, "execute", return_value=b"502")
    def test_persistent_502_fails(self, execute, sleep):
        with self.assertRaisesRegex(
            startup.StartupError, "last HTTP status: 502"
        ):
            startup.wait_https_route("/", attempts=2, delay=0)

    @patch.object(startup, "wait_frontend_ready")
    @patch.object(startup, "wait_ready")
    @patch.object(startup, "inspect")
    @patch.object(startup, "execute")
    def test_running_frontend_requires_http_readiness(
        self, execute, inspect, wait_ready, frontend_ready
    ):
        inspect.return_value = {
            "State": {"Status": "running"}
        }

        startup.ensure_running(startup.APP, "frontend")

        execute.assert_not_called()
        frontend_ready.assert_called_once()


if __name__ == "__main__":
    unittest.main()
