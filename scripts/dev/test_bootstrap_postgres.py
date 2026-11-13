"""Offline failure-safety regressions for Phase 4C PostgreSQL 18 bootstrap."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bootstrap_postgres as pg


class PgBootstrapTests(unittest.TestCase):
    def valid(self):
        return {
            "Name": "/" + pg.NAME,
            "Config": {"Labels": {
                "com.docker.compose.project": pg.PROJECT,
                "com.docker.compose.service": "postgres",
                "com.docker.compose.project.config_files": str(pg.COMPOSE_FILE),
                "com.docker.compose.project.working_dir": str(pg.ROOT),
            }, "Env": ["POSTGRES_USER=transcendence", "POSTGRES_DB=transcendence"]},
            "HostConfig": {"PortBindings": {}},
            "State": {"Running": True, "Status": "running", "Health": {"Status": "healthy"}},
            "Mounts": [{"Destination": "/var/lib/postgresql", "Type": "volume", "Name": pg.VOLUME}],
            "NetworkSettings": {"Networks": {pg.NETWORK: {}}},
        }

    def test_correct_compose_identity_accepted(self):
        self.assertIsNotNone(pg.verify_identity(self.valid()))

    def test_reject_foreign_compose(self):
        value = self.valid()
        value["Config"]["Labels"]["com.docker.compose.project.config_files"] = "/tmp/foreign.yml"
        with self.assertRaises(pg.PostgresBootstrapError):
            pg.verify_identity(value)

    def test_reject_wrong_mount(self):
        value = self.valid()
        value["Mounts"][0]["Name"] = "foreign"
        with self.assertRaises(pg.PostgresBootstrapError):
            pg.verify_identity(value)

    def test_reject_host_published_ports(self):
        value = self.valid()
        value["HostConfig"]["PortBindings"] = {"5432/tcp": [{"HostPort": "5432"}]}
        with self.assertRaises(pg.PostgresBootstrapError):
            pg.verify_identity(value)

    def test_reject_persistent_admin_password(self):
        for entry in ("POSTGRES_PASSWORD=abc", "POSTGRES_PASSWORD_FILE=/path", "POSTGRES_HOST_AUTH_METHOD=trust"):
            with self.subTest(entry=entry):
                value = self.valid()
                value["Config"]["Env"].append(entry)
                with self.assertRaises(pg.PostgresBootstrapError):
                    pg.verify_identity(value)

    def test_data_volume_probe_is_read_only(self):
        with patch.object(pg, "call", return_value=b"empty\n") as call:
            self.assertEqual(pg.volume_state(), "empty")
            args = call.call_args.args[0]
            self.assertIn("--read-only", args)
            self.assertIn("--network", args)
            self.assertIn("none", args)
            self.assertTrue(any(f"src={pg.VOLUME}" in a and "readonly" in a for a in args))

    def add_lock(self):
        # Local offline tests use the existing project lock file where present.
        self.addCleanup(lambda: None)

    def test_partial_volume_refused(self):
        with patch.object(pg, "exact_container", return_value=None), \
             patch.object(pg, "volume_state", return_value="partial"), \
             patch.object(pg, "initialize_empty_cluster") as init, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(pg.main(["apply"]), 1)
            init.assert_not_called()

    def test_initialized_volume_not_reinitialized(self):
        with patch.object(pg, "exact_container", return_value=None), \
             patch.object(pg, "volume_state", return_value="initialized"), \
             patch.object(pg, "initialize_empty_cluster") as init, \
             patch.object(pg, "start_or_keep") as start:
            self.assertEqual(pg.main(["apply"]), 0)
            init.assert_not_called()
            start.assert_called_once()

    def test_empty_volume_initialized_once(self):
        with patch.object(pg, "exact_container", return_value=None), \
             patch.object(pg, "volume_state", return_value="empty"), \
             patch.object(pg, "initialize_empty_cluster") as init, \
             patch.object(pg, "start_or_keep") as start:
            self.assertEqual(pg.main(["apply"]), 0)
            init.assert_called_once()
            start.assert_called_once()

    def test_plan_is_read_only(self):
        with patch.object(pg, "exact_container", return_value=None), \
             patch.object(pg, "volume_state") as probe, \
             patch.object(pg, "initialize_empty_cluster") as init, \
             patch.object(pg, "start_or_keep") as start:
            self.assertEqual(pg.main(["plan"]), 0)
            probe.assert_not_called()
            init.assert_not_called()
            start.assert_not_called()

    def test_existing_running_never_compose_up(self):
        with patch.object(pg, "exact_container", return_value=self.valid()), \
             patch.object(pg, "wait_for_runtime"), \
             patch.object(pg, "verify_database"), \
             patch.object(pg, "call") as call:
            pg.start_or_keep()
            call.assert_not_called()

    def test_runtime_creates_only_postgres(self):
        with patch.object(pg, "exact_container", side_effect=[None, self.valid(), self.valid()]), \
             patch.object(pg, "wait_for_runtime"), \
             patch.object(pg, "verify_database"), \
             patch.object(pg, "call", return_value=b"") as call:
            pg.start_or_keep()
            args = call.call_args.args[0]
            self.assertIn("--no-deps", args)
            self.assertEqual(args[-1], "postgres")
            self.assertIn("--env-file", args)
            self.assertEqual(call.call_args.kwargs["env"].get("POSTGRES_PASSWORD"), None)

    def test_initial_readiness_requires_final_server_not_initdb_temp_server(self):
        with patch.object(pg, "exact_container", return_value=self.valid()), \
             patch.object(pg, "call", return_value=b"") as call:
            pg.wait_for_bootstrap(attempts=1)
            args = call.call_args.args[0]
            self.assertIn("/proc/1/comm", str(args))
            self.assertIn("pg_isready", str(args))

    def test_admin_auth_query_passed_by_stdin_not_argv(self):
        with patch.object(pg, "call", return_value=b"ok\n") as call:
            pg.verify_database(pg.NAME)
            args = call.call_args.args[0]
            self.assertIn("-i", args)
            self.assertNotIn("ALTER ROLE", str(args))
            self.assertNotIn("INSPECT_SQL", str(args))
            self.assertIn(b"pg_authid", call.call_args.kwargs["stdin"])

    def test_stop_on_readiness_failure_does_not_remove_volume(self):
        with tempfile.TemporaryDirectory() as base:
            private = Path(base) / "private"
            private.mkdir(mode=0o700)
            with patch.object(pg, "STATE", Path(base)), \
                 patch.object(pg, "exact_container", return_value=None), \
                 patch.object(pg, "volume_state", return_value="empty"), \
                 patch.object(pg, "wait_for_bootstrap", side_effect=pg.PostgresBootstrapError("fail")), \
                 patch.object(pg, "call", return_value=b"container-id") as call:
                with self.assertRaises(pg.PostgresBootstrapError):
                    pg.initialize_empty_cluster()
                commands = [c.args[0] for c in call.call_args_list]
                self.assertTrue(any(c[:2] == ["docker", "stop"] for c in commands))
                self.assertFalse(any("volume" in c and "rm" in c for c in commands))
                run = next(c for c in commands if c[:2] == ["docker", "run"])
                self.assertIn("POSTGRES_PASSWORD_FILE=/run/secrets/pg-bootstrap", run)
                self.assertNotIn("POSTGRES_PASSWORD=", str(run))
                self.assertIn("--network", run)
                self.assertIn("none", run)
                self.assertIn("--log-driver", run)
                self.assertIn("none", run)
            self.assertFalse(list(private.glob("pg-init-*")))

    def test_check_never_starts_or_initializes(self):
        with patch.object(pg, "exact_container", return_value=self.valid()), \
             patch.object(pg, "wait_for_runtime"), \
             patch.object(pg, "verify_database"), \
             patch.object(pg, "start_or_keep") as start, \
             patch.object(pg, "initialize_empty_cluster") as init:
            self.assertEqual(pg.main(["check"]), 0)
            start.assert_not_called()
            init.assert_not_called()

    def test_error_does_not_echo_docker_stderr(self):
        from types import SimpleNamespace
        err = b"secret-value-DO-NOT-PRINT"
        with patch.object(pg.subprocess, "run", return_value=SimpleNamespace(returncode=7, stdout=err, stderr=err)):
            with self.assertRaises(pg.PostgresBootstrapError) as ctx:
                pg.call(["docker", "ps"])
            self.assertNotIn("secret-value", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
