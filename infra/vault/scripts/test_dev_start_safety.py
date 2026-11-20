import fcntl
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

path = Path(__file__).with_name("dev-start-runtime.py")
spec = importlib.util.spec_from_file_location(
    "startup_safety", path
)
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


class StartupSafetyTests(unittest.TestCase):

    def plan(
        self, agent_health="healthy", pending=None,
        vault_changes=None, frontend_error=None, https_error=None,
    ):
        if pending is None:
            pending = []

        def fake_inspect(project, service):
            health = (
                agent_health
                if service == "vault-backend-agent"
                else "healthy"
            )

            return {
                "State": {
                    "Status": "running",
                    "Health": {"Status": health},
                }
            }

        vault_state = {
            "initialized": True,
            "sealed": False,
            "n": 3,
            "t": 2,
            "seal_type": "shamir",
            "storage_type": "raft",
        }
        if vault_changes:
            vault_state.update(vault_changes)

        with (
            patch.object(startup, "preflight"),
            patch.object(
                startup, "inspect",
                side_effect=fake_inspect,
            ),
            patch.object(
                startup, "vault_status",
                return_value=vault_state,
            ),
            patch.object(
                startup, "audit_migrations",
                return_value=pending,
            ),
            patch.object(
                startup, "wait_frontend_ready",
                side_effect=frontend_error,
            ),
            patch.object(
                startup, "wait_https_route",
                side_effect=https_error,
            ),
        ):
            return startup.plan_runtime()

    def test_healthy_stack_plan_passes(self):
        self.assertEqual(self.plan(), 0)

    def test_unhealthy_agent_blocks_plan(self):
        self.assertEqual(
            self.plan(agent_health="unhealthy"),
            1,
        )

    def test_starting_agent_blocks_plan(self):
        self.assertEqual(
            self.plan(agent_health="starting"),
            1,
        )

    def test_pending_migrations_block_plan(self):
        self.assertEqual(
            self.plan(pending=["20261112_new"]),
            1,
        )

    def test_concurrent_startup_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = (
                root
                / "infra/vault/scripts/run-migrations.sh"
            )

            lock.parent.mkdir(parents=True)
            lock.write_text("# test lock\n")

            with lock.open("rb") as holder:
                fcntl.flock(
                    holder.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )

                with (
                    patch.object(startup, "ROOT", root),
                    patch.object(startup, "preflight") as preflight,
                ):
                    with self.assertRaisesRegex(
                        startup.StartupError,
                        "already running",
                    ):
                        startup.run_start()

                    preflight.assert_not_called()


    def test_wrong_vault_threshold_blocks_plan(self):
        self.assertEqual(
            self.plan(vault_changes={"t": 3}), 1
        )

    def test_wrong_vault_storage_blocks_plan(self):
        self.assertEqual(
            self.plan(vault_changes={"storage_type": "file"}), 1
        )

    def test_wrong_vault_seal_type_blocks_plan(self):
        self.assertEqual(
            self.plan(vault_changes={"seal_type": "awskms"}), 1
        )

    def test_unavailable_frontend_blocks_plan(self):
        self.assertEqual(
            self.plan(
                frontend_error=startup.StartupError("HTTP unavailable")
            ), 1
        )

    def test_unavailable_https_route_blocks_plan(self):
        self.assertEqual(
            self.plan(
                https_error=startup.StartupError("HTTP 502")
            ), 1
        )

    def test_unavailable_backend_api_blocks_plan(self):
        def api_failure(suffix, **kwargs):
            if suffix == "/api/v1/health":
                raise startup.StartupError("HTTP 503")

        self.assertEqual(
            self.plan(https_error=api_failure), 1
        )

if __name__ == "__main__":
    unittest.main()
