"""Offline cutover contract tests: no Docker or host writes."""
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, call

import migrate_localhost as migration


class LocalhostMigrationTests(unittest.TestCase):
    def setUp(self):
        self.current = migration.configuration.DevelopmentConfiguration(
            "transcendence.test", 8080, 8443, 8200, 5555, 168)
        self.target = replace(self.current, domain="localhost")
        self.original = "# Existing comments\nDEV_DOMAIN=transcendence.test\nCOOKIE_SECURE=true"

    def test_target_text_preserves_existing_layout_and_final_lf(self):
        self.assertEqual(migration.target_text(self.original),
                         "# Existing comments\nDEV_DOMAIN=localhost\nCOOKIE_SECURE=true\n")

    def test_refuses_missing_or_repeated_source_domain(self):
        for source in ("DEV_DOMAIN=localhost", self.original + "\nDEV_DOMAIN=transcendence.test"):
            with self.assertRaises(migration.MigrationError):
                migration.target_text(source)

    def test_plan_never_builds_or_writes(self):
        with patch.object(migration, "read_config", return_value=(self.original, None, self.current)), \
                patch.object(migration, "preflight") as preflight, \
                patch.object(migration, "compose") as compose, \
                patch.object(migration, "save_config") as save, \
                patch.object(migration, "inventory") as inventory:
            migration.migrate(False)
        preflight.assert_called_once_with(self.current, self.target)
        compose.assert_not_called()
        save.assert_not_called()
        inventory.assert_not_called()

    def test_preflight_failure_never_changes_config(self):
        with patch.object(migration, "read_config", return_value=(self.original, None, self.current)), \
                patch.object(migration, "preflight", side_effect=migration.MigrationError("refused")), \
                patch.object(migration, "compose") as compose, \
                patch.object(migration, "save_config") as save:
            with self.assertRaises(migration.MigrationError):
                migration.migrate(True)
        compose.assert_not_called()
        save.assert_not_called()

    def test_build_failure_leaves_original_config(self):
        with patch.object(migration, "read_config", return_value=(self.original, None, self.current)), \
                patch.object(migration, "preflight"), \
                patch.object(migration, "inventory", return_value={}), \
                patch.object(migration, "volume_inventory", return_value=set()), \
                patch.object(migration, "compose", side_effect=migration.MigrationError("build")), \
                patch.object(migration, "save_config") as save:
            with self.assertRaises(migration.MigrationError):
                migration.migrate(True)
        save.assert_not_called()

    def test_apply_targets_exact_services_without_dependencies(self):
        with patch.object(migration, "read_config", return_value=(self.original, None, self.current)), \
                patch.object(migration, "preflight"), \
                patch.object(migration, "inventory", return_value={}), \
                patch.object(migration, "volume_inventory", return_value=set()), \
                patch.object(migration, "compose") as compose, \
                patch.object(migration, "save_config") as save, \
                patch.object(migration, "verify_unrelated"), \
                patch.object(migration, "verify_runtime_environment") as verify, \
                patch.object(migration, "health") as health:
            migration.migrate(True)
        self.assertEqual(compose.call_args_list[0], call(self.current, "build", "frontend", timeout=900))
        self.assertEqual(compose.call_count, 3)
        for invocation in compose.call_args_list[1:]:
            self.assertEqual(invocation.args[0], self.target)
            self.assertIn("--no-deps", invocation.args)
            self.assertIn("--pull", invocation.args)
            self.assertNotIn("postgres", invocation.args)
            self.assertNotIn("vault", invocation.args)
        self.assertEqual(compose.call_args_list[1].args[-3:], ("backend", "frontend", "grafana"))
        self.assertEqual(compose.call_args_list[2].args[-1], "nginx")
        save.assert_called_once_with(migration.target_text(self.original), self.original, None)
        verify.assert_called_once_with(self.target)
        health.assert_called_once()

    def test_unrelated_inventory_changes_refused(self):
        before = {f"{migration.pg.PROJECT}-{s}-1": "old-" + s for s in migration.SERVICES}
        before["vault"] = "original"
        after = {name: "new-" + name if name != "vault" else identity for name, identity in before.items()}
        migration.verify_unrelated(before, after, {"data"}, {"data"})
        for altered, volumes in ((dict(after, vault="changed"), {"data"}), (after, set()), (before, {"data"})):
            with self.assertRaises(migration.MigrationError):
                migration.verify_unrelated(before, altered, {"data"}, volumes)

    def test_model_rejects_non_endpoint_change(self):
        def model(config):
            return {"services": {
                "backend": {"environment": {"APP_ORIGIN": config.app_origin}},
                "frontend": {"environment": {"DEV_DOMAIN": config.domain}},
                "grafana": {"environment": {"GF_SERVER_DOMAIN": config.domain,
                                             "GF_SERVER_ROOT_URL": config.grafana_root_url}},
                "nginx": {"environment": {"DEV_DOMAIN": config.domain}},
                "postgres": {"environment": {"POSTGRES_DB": "transcendence"}},
            }}
        old, new = model(self.current), model(self.target)
        with patch.object(migration, "compose", side_effect=[json.dumps(old), json.dumps(new)]):
            migration.verify_target_model(self.current, self.target)
        new["services"]["postgres"]["environment"]["POSTGRES_DB"] = "other"
        with patch.object(migration, "compose", side_effect=[json.dumps(old), json.dumps(new)]):
            with self.assertRaises(migration.MigrationError):
                migration.verify_target_model(self.current, self.target)

    def test_private_group_writable_config_read_and_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "development.env"
            original = (self.original + "\nDEV_HTTP_PORT=8080\nDEV_HTTPS_PORT=8443\n"
                        "DEV_VAULT_UI_PORT=8200\nDEV_PRISMA_UI_PORT=5555\nSESSION_TTL_HOURS=168\n")
            path.write_text(original, encoding="utf-8")
            path.chmod(0o664)
            with patch.object(migration.configuration, "CONFIG_FILE", path), \
                    patch.object(migration.configuration, "private_primary_group", return_value=True):
                text, info, config = migration.read_config()
                self.assertEqual(config, self.current)
                migration.save_config(migration.target_text(text), text, info)
                self.assertEqual(path.stat().st_mode & 0o777, 0o664)
            self.assertEqual(os.stat(path).st_uid, os.getuid())

    def test_cutover_refuses_permissions_changed_after_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "development.env"
            path.write_text(self.original, encoding="utf-8")
            path.chmod(0o644)
            info = path.stat()
            path.chmod(0o600)
            with patch.object(migration.configuration, "CONFIG_FILE", path):
                with self.assertRaisesRegex(migration.MigrationError, "changed during cutover"):
                    migration.save_config("new", self.original, info)
            self.assertEqual(path.read_text(), self.original)

    def test_atomic_update_detects_file_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "development.env"
            path.write_text(self.original)
            info = path.stat()
            with patch.object(migration.configuration, "CONFIG_FILE", path):
                migration.save_config(migration.target_text(self.original), self.original, info)
                self.assertIn("DEV_DOMAIN=localhost\n", path.read_text())
                with self.assertRaises(migration.MigrationError):
                    migration.save_config("bad", self.original, info)


if __name__ == "__main__":
    unittest.main()
