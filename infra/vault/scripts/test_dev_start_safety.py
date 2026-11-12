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

    def plan(self, agent_health="healthy", pending=None):
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

        with (
            patch.object(startup, "preflight"),
            patch.object(
                startup, "inspect",
                side_effect=fake_inspect,
            ),
            patch.object(
                startup, "vault_status",
                return_value={
                    "initialized": True,
                    "sealed": False,
                },
            ),
            patch.object(
                startup, "audit_migrations",
                return_value=pending,
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


if __name__ == "__main__":
    unittest.main()
