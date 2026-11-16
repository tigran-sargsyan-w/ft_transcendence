"""Phase 5B read-only security / idempotency regression tests (no Docker)."""

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bootstrap_backend as backend


def valid(*, running=True, health="healthy"):
    return {
        "Name": "/" + backend.NAME,
        "Config": {
            "Labels": {
                "com.docker.compose.project": backend.pg.PROJECT,
                "com.docker.compose.service": "backend",
                "com.docker.compose.project.config_files": str(backend.pg.COMPOSE_FILE),
                "com.docker.compose.project.working_dir": str(backend.pg.ROOT),
            },
            "Image": backend.EXPECTED_IMAGE,
            "User": "10001:20000",
            "Env": ["DATABASE_URL_FILE=" + backend.DATABASE_FILE,
                    "GRAPH_ENGINE_URL=http://graph-engine:8000"],
            "Cmd": ["sh", "-ec", "sh scripts/wait-for-database-secret.sh; exec npm run start:prod"],
        },
        "HostConfig": {
            "ReadonlyRootfs": True, "Privileged": False,
            "PortBindings": {}, "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
        },
        "Mounts": [
            {"Destination": "/run/secrets/backend", "Type": "volume",
             "Name": backend.agent.RENDERED_VOLUME, "RW": False},
            {"Destination": "/app/prisma", "Type": "bind",
             "Source": str(backend.pg.ROOT / "backend/prisma"), "RW": False},
        ],
        "NetworkSettings": {"Networks": {backend.DEFAULT_NETWORK: {}}},
        "State": {"Status": "running" if running else "exited",
                  "Running": running, "Health": {"Status": health}},
    }


class BackendTests(unittest.TestCase):
    def test_valid_identity(self):
        self.assertIsNotNone(backend.verify_identity(valid()))

    def test_untrusted_project(self):
        item = valid()
        item["Config"]["Labels"]["com.docker.compose.project"] = "other"
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_untrusted_config_file(self):
        item = valid()
        item["Config"]["Labels"]["com.docker.compose.project.config_files"] = "/tmp/compose.yml"
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_root_user_refused(self):
        item = valid(); item["Config"]["User"] = "0:0"
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_wrong_image(self):
        item = valid(); item["Config"]["Image"] = "node:latest"
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_must_have_readonly_rootfs(self):
        item = valid(); item["HostConfig"]["ReadonlyRootfs"] = False
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_wrong_docker_field_name_is_not_accepted(self):
        item = valid()
        item["HostConfig"].pop("ReadonlyRootfs")
        item["HostConfig"]["ReadOnlyRootfs"] = True
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_missing_readonly_rootfs_is_refused(self):
        item = valid()
        item["HostConfig"].pop("ReadonlyRootfs")
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_privileged_refused(self):
        item = valid(); item["HostConfig"]["Privileged"] = True
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_published_port_refused(self):
        item = valid(); item["HostConfig"]["PortBindings"] = {"3000/tcp": [{"HostPort": "3000"}]}
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_missing_cap_drop_refused(self):
        item = valid(); item["HostConfig"]["CapDrop"] = []
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_no_new_privileges_required(self):
        item = valid(); item["HostConfig"]["SecurityOpt"] = []
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_extra_vault_network_refused(self):
        item = valid(); item["NetworkSettings"]["Networks"][backend.agent.PRIVATE_NETWORK] = {}
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_rw_secret_refused(self):
        item = valid(); item["Mounts"][0]["RW"] = True
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_wrong_secret_volume_refused(self):
        item = valid(); item["Mounts"][0]["Name"] = "untrusted"
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_rw_prisma_refused(self):
        item = valid(); item["Mounts"][1]["RW"] = True
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_extra_mount_refused(self):
        item = valid(); item["Mounts"].append({"Destination": "/etc/shadow"})
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_no_environment_database_url(self):
        item = valid(); item["Config"]["Env"].append("DATABASE_URL=supersecret")
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_no_environment_vault_token(self):
        item = valid(); item["Config"]["Env"].append("VAULT_TOKEN=supersecret")
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_requires_file_based_credentials(self):
        item = valid(); item["Config"]["Env"] = []
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_unexpected_start_command_refused(self):
        item = valid(); item["Config"]["Cmd"] = ["sh", "-c", "echo running"]
        with self.assertRaises(backend.BackendBootstrapError):
            backend.verify_identity(item)

    def test_http_probe_does_not_print_body(self):
        self.assertNotIn("console.log", backend.HTTP_HEALTH_PROBE)
        self.assertIn("/api/v1/health", backend.HTTP_HEALTH_PROBE)
        self.assertIn("body?.data?.database", backend.HTTP_HEALTH_PROBE)
        with patch.object(backend, "run") as run:
            backend.verify_http_health()
            self.assertEqual(run.call_args.args[0][0:4], ["docker", "exec", backend.NAME, "node"])

    def test_plan_missing_does_not_touch_dependencies(self):
        with patch.object(backend, "container", return_value=None), \
             patch.object(backend, "require_prerequisites") as req, \
             patch.object(backend, "start_or_keep") as start:
            self.assertEqual(backend.main(["plan"]), 0)
            req.assert_not_called(); start.assert_not_called()

    def test_plan_existing_only_inspects(self):
        with patch.object(backend, "container", return_value=valid()), \
             patch.object(backend, "verify_http_health") as http, \
             patch.object(backend, "require_prerequisites") as req:
            self.assertEqual(backend.main(["plan"]), 0)
            http.assert_not_called(); req.assert_not_called()

    def test_check_is_readonly(self):
        with patch.object(backend, "require_prerequisites"), \
             patch.object(backend, "container", return_value=valid()), \
             patch.object(backend, "verify_http_health"), \
             patch.object(backend, "start_or_keep") as start:
            self.assertEqual(backend.main(["check"]), 0)
            start.assert_not_called()

    def test_check_nonhealthy_refused(self):
        with patch.object(backend, "require_prerequisites"), \
             patch.object(backend, "container", return_value=valid(health="starting")), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(backend.main(["check"]), 1)

    def test_check_missing_refused(self):
        with patch.object(backend, "require_prerequisites"), \
             patch.object(backend, "container", return_value=None), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(backend.main(["check"]), 1)

    def test_apply_requires_agent(self):
        with patch.object(backend.agent, "main", return_value=1):
            with self.assertRaises(backend.BackendBootstrapError):
                backend.require_prerequisites()

    def test_existing_running_never_recreated(self):
        with patch.object(backend, "container", return_value=valid()), \
             patch.object(backend, "run") as run:
            backend.start_or_keep()
            run.assert_not_called()

    def test_existing_stopped_only_docker_start(self):
        with patch.object(backend, "container", return_value=valid(running=False)), \
             patch.object(backend, "run") as run:
            backend.start_or_keep()
            self.assertEqual(run.call_args.args[0], ["docker", "start", backend.NAME])

    def test_new_container_compose_nondeps_norecreate(self):
        with patch.object(backend, "container", side_effect=[None, valid()]), \
             patch.object(backend, "run") as run:
            backend.start_or_keep()
            command = run.call_args.args[0]
            self.assertIn("--no-deps", command)
            self.assertIn("--no-recreate", command)
            self.assertIn("--build", command)
            self.assertEqual(command[-1], "backend")
            self.assertNotIn("--profile", command)

    def test_new_container_provenance_failure_stops(self):
        bad = valid(); bad["Config"]["User"] = "root"
        with patch.object(backend, "container", side_effect=[None, bad]), \
             patch.object(backend, "run"):
            with self.assertRaises(backend.BackendBootstrapError):
                backend.start_or_keep()

    def test_wait_healthy_probes_real_http(self):
        with patch.object(backend, "container", return_value=valid()), \
             patch.object(backend, "verify_http_health") as http:
            backend.wait_backend(attempts=1)
            http.assert_called_once()

    def test_wait_exit_fails_without_removing(self):
        with patch.object(backend, "container", return_value=valid(running=False)), \
             patch.object(backend, "verify_http_health") as http:
            with self.assertRaises(backend.BackendBootstrapError):
                backend.wait_backend(attempts=1)
            http.assert_not_called()

    def test_wait_timeout(self):
        item = valid(health="starting")
        with patch.object(backend, "container", return_value=item), \
             patch.object(backend, "time") as sleeper:
            with self.assertRaises(backend.BackendBootstrapError):
                backend.wait_backend(attempts=2, delay=0)
            self.assertEqual(sleeper.sleep.call_count, 1)

    def test_subprocess_failure_redacts_output(self):
        secret = b"postgresql://user:supersecret@postgres/db"
        with patch.object(backend.subprocess, "run", return_value=SimpleNamespace(returncode=4, stdout=secret, stderr=secret)):
            with self.assertRaises(backend.BackendBootstrapError) as err:
                backend.run(["docker", "ps"])
            self.assertNotIn("supersecret", str(err.exception))

    def test_subprocess_timeout_redacts_output(self):
        with patch.object(backend.subprocess, "run", side_effect=subprocess.TimeoutExpired(
            cmd=["docker", "ps"], timeout=2, output=b"SECRET"
        )):
            with self.assertRaises(backend.BackendBootstrapError) as err:
                backend.run(["docker", "ps"])
            self.assertNotIn("SECRET", str(err.exception))

    def test_apply_fails_if_prerequisite_failed(self):
        with patch.object(backend, "require_prerequisites", side_effect=backend.BackendBootstrapError("no")), \
             patch.object(backend, "start_or_keep") as start, \
             contextlib.redirect_stderr(io.StringIO()):
            with tempfile.TemporaryDirectory() as td:
                lock = Path(td) / "lockfile"; lock.touch()
                with patch.object(backend.pg, "LOCK", lock):
                    self.assertEqual(backend.main(["apply"]), 1)
            start.assert_not_called()

    def test_apply_proper_order(self):
        order = []
        with tempfile.TemporaryDirectory() as td:
            lock = Path(td) / "lock"; lock.touch()
            with patch.object(backend.pg, "LOCK", lock), \
                 patch.object(backend, "require_prerequisites", side_effect=lambda: order.append("pre")), \
                 patch.object(backend, "start_or_keep", side_effect=lambda: order.append("start")), \
                 patch.object(backend, "wait_backend", side_effect=lambda: order.append("health")):
                self.assertEqual(backend.main(["apply"]), 0)
        self.assertEqual(order, ["pre", "pre", "start", "health"])


if __name__ == "__main__":
    unittest.main()
