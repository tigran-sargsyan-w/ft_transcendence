"""Offline safety and workflow tests for fresh-machine Prisma migrations."""

import contextlib
import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bootstrap_migrations as migration


class BootstrapMigrationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        root = self.path / "migrations"
        root.mkdir()
        self.names = ["20260920161343_init_user", "20260928163702_add_sessions"]
        for name in self.names:
            child = root / name
            child.mkdir()
            (child / "migration.sql").write_text("SELECT 1;\n", encoding="utf-8")
        (root / "migration_lock.toml").write_text('provider = "postgresql"\n')
        self.patcher = patch.object(migration, "MIGRATIONS", root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def fixture_history(self, names=None):
        expected = migration.expected_migrations()
        return "\n".join(f"{n}\t{expected[n]}\tapplied" for n in (names or self.names))

    def test_local_migrations_have_strict_names_and_real_sha256(self):
        actual = migration.expected_migrations()
        self.assertEqual(set(actual), set(self.names))
        self.assertEqual(actual[self.names[0]], hashlib.sha256(b"SELECT 1;\n").hexdigest())

    def test_local_migration_symlink_refused(self):
        item = migration.MIGRATIONS / self.names[0] / "migration.sql"
        item.unlink()
        item.symlink_to(migration.MIGRATIONS / self.names[1] / "migration.sql")
        with self.assertRaises(migration.MigrationBootstrapError):
            migration.expected_migrations()

    def test_local_unexpected_migration_file_refused(self):
        (migration.MIGRATIONS / "surprise.txt").write_text("abc")
        with self.assertRaises(migration.MigrationBootstrapError):
            migration.expected_migrations()

    def test_empty_migration_directory_refused(self):
        with tempfile.TemporaryDirectory() as td, patch.object(migration, "MIGRATIONS", Path(td)):
            with self.assertRaises(migration.MigrationBootstrapError):
                migration.expected_migrations()

    def test_missing_schema_empty_database_is_pending(self):
        with patch.object(migration, "sql_value", return_value="absent"), \
             patch.object(migration.pg, "db_query", return_value="empty") as query:
            self.assertEqual(migration.migration_state(), "pending")
            self.assertEqual(query.call_count, 1)

    def test_missing_migration_table_with_manual_tables_refused(self):
        with patch.object(migration, "sql_value", return_value="absent"), \
             patch.object(migration.pg, "db_query", return_value="unknown"):
            with self.assertRaisesRegex(migration.MigrationBootstrapError, "Unmanaged"):
                migration.migration_state()

    def test_partial_schema_is_refused(self):
        with patch.object(migration, "sql_value", return_value="partial"):
            with self.assertRaises(migration.MigrationBootstrapError):
                migration.migration_state()

    def test_completed_history_and_grants_ready(self):
        with patch.object(migration, "sql_value", side_effect=("present", "ready")), \
             patch.object(migration.pg, "db_query", return_value=self.fixture_history()):
            self.assertEqual(migration.migration_state(), "ready")

    def test_completed_history_without_grants_is_pending(self):
        with patch.object(migration, "sql_value", side_effect=("present", "pending")), \
             patch.object(migration.pg, "db_query", return_value=self.fixture_history()):
            self.assertEqual(migration.migration_state(), "pending")

    def test_missing_migration_in_history_is_pending(self):
        with patch.object(migration, "sql_value", return_value="present"), \
             patch.object(migration.pg, "db_query", return_value=self.fixture_history([self.names[0]])):
            self.assertEqual(migration.migration_state(), "pending")

    def test_changed_checksum_refused(self):
        record = f"{self.names[0]}\t{'0'*64}\tapplied"
        with patch.object(migration, "sql_value", return_value="present"), \
             patch.object(migration.pg, "db_query", return_value=record):
            with self.assertRaisesRegex(migration.MigrationBootstrapError, "drift"):
                migration.migration_state()

    def test_unknown_migration_refused(self):
        record = f"20251111111111_unknown\t{'0'*64}\tapplied"
        with patch.object(migration, "sql_value", return_value="present"), \
             patch.object(migration.pg, "db_query", return_value=record):
            with self.assertRaises(migration.MigrationBootstrapError):
                migration.migration_state()

    def test_failed_and_rolled_back_migration_refused(self):
        for status in ("failed", "rolled_back"):
            with self.subTest(status=status):
                record = self.fixture_history([self.names[0]]).replace("applied", status)
                with patch.object(migration, "sql_value", return_value="present"), \
                     patch.object(migration.pg, "db_query", return_value=record):
                    with self.assertRaises(migration.MigrationBootstrapError):
                        migration.migration_state()

    def test_duplicate_migration_records_refused(self):
        record = self.fixture_history([self.names[0]])
        with patch.object(migration, "sql_value", return_value="present"), \
             patch.object(migration.pg, "db_query", return_value=record + "\n" + record):
            with self.assertRaises(migration.MigrationBootstrapError):
                migration.migration_state()

    def test_schema_grants_require_runtime_least_privilege(self):
        q = migration.GRANTS_SQL
        self.assertIn("_prisma_migrations", q)
        self.assertIn("'transcendence_app'", q)
        self.assertIn("'transcendence_migrator'", q)
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            self.assertIn(privilege, q)
        self.assertNotIn("ALTER ROLE", q)

    def test_migrator_approle_inventory_missing(self):
        with patch.object(migration.roles, "sanitized_run", return_value=SimpleNamespace(returncode=2)) as run:
            self.assertEqual(migration.approle_inventory(), "missing")
            cmd = run.call_args.args[0]
            self.assertIn("--network", cmd)
            self.assertIn("none", cmd)
            self.assertIn("readonly", str(cmd))

    def test_migrator_approle_partial_refused(self):
        with patch.object(migration.roles, "sanitized_run", return_value=SimpleNamespace(returncode=3)):
            with self.assertRaises(migration.MigrationBootstrapError):
                migration.approle_inventory()

    def test_existing_approle_not_reissued(self):
        with patch.object(migration.roles, "sanitized_run"), \
             patch.object(migration, "approle_inventory", return_value="present"), \
             patch.object(migration.config, "recovery_token") as token:
            migration.ensure_migrator_approle({})
            token.assert_not_called()

    def test_migrator_new_approle_token_on_stdin_only(self):
        with patch.object(migration.roles, "sanitized_run") as run, \
             patch.object(migration, "approle_inventory", side_effect=("missing", "present")), \
             patch.object(migration.config, "recovery_token", return_value="SECRET-ROOT-TOKEN"):
            migration.ensure_migrator_approle({"FT_DEV_TLS_DIR": "/tmp/certs"})
            self.assertEqual(run.call_count, 2)
            call = run.call_args_list[-1]
            self.assertIn("vault-migrator-provision", call.args[0])
            self.assertEqual(call.kwargs["stdin"], b"SECRET-ROOT-TOKEN\n")
            self.assertNotIn("SECRET-ROOT-TOKEN", str(call.args[0]) + str(call.kwargs["env"]))

    def test_migrator_incomplete_creation_refused(self):
        with patch.object(migration.roles, "sanitized_run"), \
             patch.object(migration, "approle_inventory", side_effect=("missing", "missing")), \
             patch.object(migration.config, "recovery_token", return_value="SECRET"):
            with self.assertRaises(migration.MigrationBootstrapError):
                migration.ensure_migrator_approle({})

    def test_workflow_uses_existing_runtime_and_shared_fd(self):
        with patch.object(migration.roles, "sanitized_run") as run:
            migration.run_existing_migrations({"FT_EXISTING_RUNTIME_ONLY": "1"})
            self.assertEqual(run.call_args.kwargs["pass_fds"], (9,))
            self.assertEqual(run.call_args.kwargs["env"]["FT_EXISTING_RUNTIME_ONLY"], "1")
            self.assertEqual(run.call_args.args[0][0], "bash")
            self.assertIn("run-migrations.sh", str(run.call_args.args[0]))

    def test_repeated_apply_keeps_ready_migrations(self):
        with tempfile.TemporaryDirectory() as td:
            lock = Path(td) / "run-migrations.sh"
            lock.write_text("lock\n")
            with patch.object(migration.pg, "LOCK", lock), \
                 patch.object(migration, "migration_state", return_value="ready"), \
                 patch.object(migration, "ensure_migrator_approle") as approle, \
                 patch.object(migration, "run_existing_migrations") as workflow:
                migration.apply_migrations()
                approle.assert_not_called()
                workflow.assert_not_called()

    def test_apply_holds_lock_before_issuing_credentials(self):
        with tempfile.TemporaryDirectory() as td:
            lock = Path(td) / "lock.sh"
            lock.write_text("lock\n")
            with patch.object(migration.pg, "LOCK", lock), \
                 patch.object(migration, "migration_state", return_value="pending"), \
                 patch.object(migration, "ensure_migrator_approle") as approle, \
                 patch.object(migration, "run_existing_migrations") as workflow:
                migration.apply_migrations()
                approle.assert_called_once()
                workflow.assert_called_once()

    def test_child_reuses_parent_lock_description(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = root / "run-migrations.sh"
            script.write_text("#!/bin/bash\nset -eu\nflock -n 9\n")
            with patch.object(migration.pg, "LOCK", script), \
                 patch.object(migration, "WORKFLOW", script), \
                 patch.object(migration, "migration_state", return_value="pending"), \
                 patch.object(migration, "ensure_migrator_approle"):
                migration.apply_migrations()

    def test_plan_does_not_read_root_token_or_run_migrations(self):
        with patch.object(migration.pg, "main", return_value=0), \
             patch.object(migration.pg, "exact_container", return_value={"State":{"Running":True}}), \
             patch.object(migration.pg, "verify_identity"), \
             patch.object(migration, "migration_state", return_value="pending"), \
             patch.object(migration, "apply_migrations") as apply, \
             patch.object(migration.config, "recovery_token") as token:
            self.assertEqual(migration.main(["plan"]), 0)
            apply.assert_not_called()
            token.assert_not_called()

    def test_check_pending_fails_without_applying(self):
        with patch.object(migration.roles, "main", return_value=0), \
             patch.object(migration, "migration_state", return_value="pending"), \
             patch.object(migration, "apply_migrations") as apply, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(migration.main(["check"]), 1)
            apply.assert_not_called()

    def test_check_ready_passes_without_change(self):
        with patch.object(migration.roles, "main", return_value=0), \
             patch.object(migration, "migration_state", return_value="ready"), \
             patch.object(migration, "apply_migrations") as apply:
            self.assertEqual(migration.main(["check"]), 0)
            apply.assert_not_called()

    def test_apply_requires_postgres_role_readiness(self):
        with patch.object(migration.roles, "main", return_value=1), \
             patch.object(migration, "apply_migrations") as apply, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(migration.main(["apply"]), 1)
            apply.assert_not_called()

    def test_apply_checks_final_schema(self):
        with patch.object(migration.roles, "main", return_value=0), \
             patch.object(migration, "migration_state", return_value="pending"), \
             patch.object(migration, "apply_migrations") as apply, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(migration.main(["apply"]), 1)
            apply.assert_called_once()

    def test_apply_after_success_is_ready(self):
        with patch.object(migration.roles, "main", return_value=0), \
             patch.object(migration, "migration_state", return_value="ready"), \
             patch.object(migration, "apply_migrations") as apply:
            self.assertEqual(migration.main(["apply"]), 0)
            apply.assert_called_once()


if __name__ == "__main__":
    unittest.main()
