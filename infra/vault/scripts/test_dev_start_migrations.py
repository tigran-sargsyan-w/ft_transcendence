import importlib.util
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

path = Path(__file__).with_name("dev-start-runtime.py")
spec = importlib.util.spec_from_file_location(
    "startup_migrations", path
)
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


class MigrationStartupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        self.root = Path(temp.name)
        lock = self.root / "infra/vault/scripts/run-migrations.sh"
        lock.parent.mkdir(parents=True)
        lock.write_text("# test lock\n")

    def mocks(self, stack):
        stack.enter_context(patch.object(startup, "ROOT", self.root))

        for target in (
            "preflight",
            "ensure_running",
            "verify_vault",
            "verify_app",
        ):
            stack.enter_context(patch.object(startup, target))

        execute = stack.enter_context(
            patch.object(startup, "execute")
        )
        migrate = stack.enter_context(
            patch.object(startup, "run_migrations_with_lock")
        )
        ensure = startup.ensure_running
        return execute, migrate, ensure

    def test_current_migrations_skip_workflow(self):
        with ExitStack() as stack:
            execute, migrate, ensure = self.mocks(stack)

            stack.enter_context(
                patch.object(
                    startup,
                    "audit_migrations",
                    return_value=[],
                )
            )

            startup.run_start(allow_migrations=True)

            migrate.assert_not_called()
            execute.assert_not_called()
            self.assertTrue(
                any(call.args == (startup.APP, "backend")
                    for call in ensure.call_args_list)
            )

    def test_pending_requires_explicit_mode(self):
        with ExitStack() as stack:
            execute, migrate, ensure = self.mocks(stack)

            stack.enter_context(
                patch.object(
                    startup,
                    "audit_migrations",
                    return_value=["20261112_new"],
                )
            )

            with self.assertRaisesRegex(
                startup.StartupError, "Pending Prisma"
            ):
                startup.run_start()

            migrate.assert_not_called()
            execute.assert_not_called()
            self.assertFalse(
                any(call.args == (startup.APP, "backend")
                    for call in ensure.call_args_list)
            )

    def test_authorized_migration_stops_backend(self):
        with ExitStack() as stack:
            execute, migrate, ensure = self.mocks(stack)

            stack.enter_context(
                patch.object(
                    startup,
                    "audit_migrations",
                    side_effect=[["20261112_new"], []],
                )
            )
            stack.enter_context(
                patch.object(
                    startup,
                    "inspect",
                    return_value={"State": {"Status": "running"}},
                )
            )

            startup.run_start(allow_migrations=True)

            migrate.assert_called_once()
            self.assertEqual(
                execute.call_args_list[0].args[0][:3],
                ["docker", "stop", "--time"],
            )
            self.assertTrue(
                any(call.args == (startup.APP, "backend")
                    for call in ensure.call_args_list)
            )

    def test_migration_failure_keeps_backend_stopped(self):
        with ExitStack() as stack:
            execute, migrate, ensure = self.mocks(stack)

            stack.enter_context(
                patch.object(
                    startup,
                    "audit_migrations",
                    return_value=["20261112_new"],
                )
            )
            stack.enter_context(
                patch.object(
                    startup,
                    "inspect",
                    return_value={"State": {"Status": "running"}},
                )
            )
            migrate.side_effect = startup.StartupError(
                "Migration workflow failed"
            )

            with self.assertRaises(startup.StartupError):
                startup.run_start(allow_migrations=True)

            execute.assert_called_once()
            self.assertFalse(
                any(call.args == (startup.APP, "backend")
                    for call in ensure.call_args_list)
            )


if __name__ == "__main__":
    unittest.main()
