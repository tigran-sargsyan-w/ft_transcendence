"""Offline behavior/security tests for Phase 4D PostgreSQL role setup."""

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bootstrap_postgres_roles as roles


class PostgreSQLRolesTests(unittest.TestCase):
    def test_fixed_sql_only_returns_boolean_state(self):
        self.assertIn("rolpassword LIKE 'SCRAM-SHA-256$%'", roles.ROLE_STATUS_SQL)
        self.assertIn("NOT (rolsuper", roles.ROLE_STATUS_SQL)
        self.assertIn("has_schema_privilege", roles.ROLE_STATUS_SQL)
        # Missing roles must yield 'pending' rather than an SQL error.
        self.assertIn("EXISTS (", roles.ROLE_STATUS_SQL)
        self.assertIn("has_schema_privilege(oid", roles.ROLE_STATUS_SQL)
        self.assertNotIn("has_schema_privilege('transcendence_app'", roles.ROLE_STATUS_SQL)
        self.assertNotIn("ALTER ROLE", roles.ROLE_STATUS_SQL)

    def test_safe_env_does_not_inherit_database_urls_or_token(self):
        with patch.dict(os.environ, {
            "VAULT_TOKEN": "secret-token", "DATABASE_URL": "secret-db",
            "POSTGRES_PASSWORD": "secret-password", "FT_EXISTING_RUNTIME_ONLY": "0",
        }, clear=False):
            env = roles.safe_env()
        self.assertNotIn("VAULT_TOKEN", env)
        self.assertNotIn("DATABASE_URL", env)
        self.assertNotIn("POSTGRES_PASSWORD", env)
        self.assertEqual(env["FT_EXISTING_RUNTIME_ONLY"], "1")
        self.assertEqual(env["FT_APP_COMPOSE_PROJECT"], "transcendence-app-dev")

    def test_approle_inventory_no_file_returns_missing(self):
        with patch.object(roles, "sanitized_run", return_value=SimpleNamespace(returncode=2)) as run:
            self.assertEqual(roles.approle_inventory(), "missing")
            args = run.call_args.args[0]
            self.assertIn("--network", args)
            self.assertIn("none", args)
            self.assertIn("--read-only", args)
            self.assertIn("readonly", str(args))
            self.assertNotIn("vault-token", str(args))

    def test_approle_inventory_existing_returns_present(self):
        with patch.object(roles, "sanitized_run", return_value=SimpleNamespace(returncode=0)):
            self.assertEqual(roles.approle_inventory(), "present")

    def test_approle_inventory_partial_refuses(self):
        with patch.object(roles, "sanitized_run", return_value=SimpleNamespace(returncode=3)):
            with self.assertRaises(roles.RolesBootstrapError):
                roles.approle_inventory()

    def test_existing_credentials_never_reissued(self):
        with patch.object(roles, "sanitized_run"), \
             patch.object(roles, "approle_inventory", return_value="present"), \
             patch.object(roles.config, "recovery_token") as token:
            roles.ensure_provisioner_approle({})
            token.assert_not_called()

    def test_new_approle_token_goes_only_to_stdin(self):
        with patch.object(roles, "sanitized_run") as run, \
             patch.object(roles, "approle_inventory", side_effect=("missing", "present")), \
             patch.object(roles.config, "recovery_token", return_value="sensitive-root-token"):
            roles.ensure_provisioner_approle({"FT_DEV_TLS_DIR": "/tmp/tls"})
            self.assertEqual(run.call_count, 2)
            helper = run.call_args_list[1]
            self.assertIn("vault-postgres-provisioner-provision", helper.args[0])
            self.assertNotIn("sensitive-root-token", str(helper.args[0]))
            self.assertEqual(helper.kwargs["stdin"], b"sensitive-root-token\n")
            self.assertNotIn("sensitive-root-token", str(helper.kwargs["env"]))

    def test_new_approle_incomplete_fails(self):
        with patch.object(roles, "sanitized_run"), \
             patch.object(roles, "approle_inventory", side_effect=("missing", "missing")), \
             patch.object(roles.config, "recovery_token", return_value="secret"):
            with self.assertRaises(roles.RolesBootstrapError):
                roles.ensure_provisioner_approle({})

    def test_command_failure_hides_output(self):
        secret = b"secret-from-docker"
        fake = SimpleNamespace(returncode=42, stdout=secret, stderr=secret)
        with patch.object(roles.subprocess, "run", return_value=fake):
            with self.assertRaises(roles.RolesBootstrapError) as err:
                roles.sanitized_run(["docker", "ps"])
            self.assertNotIn("secret-from-docker", str(err.exception))

    def test_command_timeout_hides_output(self):
        with patch.object(roles.subprocess, "run", side_effect=subprocess.TimeoutExpired(
            cmd=["docker", "ps"], timeout=5, output=b"sensitive-data"
        )):
            with self.assertRaises(roles.RolesBootstrapError) as err:
                roles.sanitized_run(["docker", "ps"])
            self.assertNotIn("sensitive-data", str(err.exception))

    def test_existing_workflow_uses_shared_lock_fd_and_existing_runtime(self):
        with patch.object(roles, "sanitized_run") as run:
            roles.run_existing_provisioner({"FT_EXISTING_RUNTIME_ONLY": "1"}, 9)
            self.assertEqual(run.call_args.kwargs["pass_fds"], (9,))
            self.assertEqual(run.call_args.kwargs["env"]["FT_EXISTING_RUNTIME_ONLY"], "1")
            self.assertIn("provision-postgres.sh", str(run.call_args.args[0]))
        with self.assertRaises(roles.RolesBootstrapError):
            roles.run_existing_provisioner({}, 8)

    def test_plan_never_reads_credentials_or_runs_provisioning(self):
        item = {"State": {"Running": True}}
        with patch.object(roles.pg, "main", return_value=0), \
             patch.object(roles.pg, "exact_container", return_value=item), \
             patch.object(roles.pg, "verify_identity"), \
             patch.object(roles, "role_state", return_value="pending"), \
             patch.object(roles, "ensure_provisioner_approle") as app, \
             patch.object(roles, "run_existing_provisioner") as provisioner, \
             patch.object(roles.config, "recovery_token") as token:
            self.assertEqual(roles.main(["plan"]), 0)
            token.assert_not_called()
            app.assert_not_called()
            provisioner.assert_not_called()

    def test_check_does_not_modify_credentials(self):
        with patch.object(roles, "require_prerequisites"), \
             patch.object(roles, "role_state", return_value="ready"), \
             patch.object(roles, "apply_roles") as apply:
            self.assertEqual(roles.main(["check"]), 0)
            apply.assert_not_called()

    def test_check_missing_roles_fails_closed(self):
        with patch.object(roles, "require_prerequisites"), \
             patch.object(roles, "role_state", return_value="pending"), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(roles.main(["check"]), 1)

    def test_apply_checks_preconditions_before_mutating(self):
        with patch.object(roles.pg, "main", return_value=1), \
             patch.object(roles, "apply_roles") as apply, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(roles.main(["apply"]), 1)
            apply.assert_not_called()

    def test_apply_checks_postcondition(self):
        with patch.object(roles, "require_prerequisites"), \
             patch.object(roles, "apply_roles") as apply, \
             patch.object(roles, "role_state", return_value="ready"):
            self.assertEqual(roles.main(["apply"]), 0)
            apply.assert_called_once()

    def test_apply_does_not_accept_incomplete_postcondition(self):
        with patch.object(roles, "require_prerequisites"), \
             patch.object(roles, "apply_roles"), \
             patch.object(roles, "role_state", return_value="pending"), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(roles.main(["apply"]), 1)

    def test_real_child_reuses_shared_flock(self):
        # Exercise the exact lock handoff to a real bash child, without Docker.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            scripts = root / "infra/vault/scripts"
            scripts.mkdir(parents=True)
            command = scripts / "provision-postgres.sh"
            command.write_text(
                '#!/bin/bash\nset -eu\n'
                'test "$FT_EXISTING_RUNTIME_ONLY" = 1\n'
                'flock -n 9\n'
            )
            lock = scripts / "run-migrations.sh"
            lock.write_text("#!/bin/bash\n")
            with patch.object(roles.pg, "LOCK", lock), \
                 patch.object(roles, "ROOT", root), \
                 patch.object(roles, "ensure_provisioner_approle"):
                roles.apply_roles()

    def test_lock_is_inherited_as_fd9_not_reopened(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "lock.sh"
            target.write_text("#!/bin/sh\n")
            with patch.object(roles.pg, "LOCK", target), \
                 patch.object(roles, "ensure_provisioner_approle") as setup, \
                 patch.object(roles, "run_existing_provisioner") as run:
                roles.apply_roles()
                setup.assert_called_once()
                self.assertEqual(run.call_args.args[1], 9)


if __name__ == "__main__":
    unittest.main()
