"""Offline Phase 5D frontend tests. No Docker or network dependencies."""

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bootstrap_frontend as frontend


def fixture(status="running", *, image=None):
    return {
        "Name": "/" + frontend.NAME,
        "Config": {
            "Labels": {
                "com.docker.compose.project": frontend.pg.PROJECT,
                "com.docker.compose.service": frontend.SERVICE,
                "com.docker.compose.project.config_files": str(frontend.pg.COMPOSE_FILE),
                "com.docker.compose.project.working_dir": str(frontend.pg.ROOT),
            },
            "Image": image or frontend.IMAGES[0],
            "Env": ["BACKEND_URL=http://backend:3000", "NODE_ENV=development"],
            "Cmd": ["npm", "run", "dev", "--", "--host", "0.0.0.0"],
        },
        "HostConfig": {"Privileged": False, "PortBindings": {}},
        "NetworkSettings": {"Networks": {frontend.DEFAULT_NETWORK: {}}},
        "Mounts": [],
        "State": {"Running": status == "running", "Status": status},
    }


class FrontendTests(unittest.TestCase):
    def test_expected_identity(self):
        frontend.verify_identity(fixture())

    def test_image_with_latest_tag_allowed(self):
        frontend.verify_identity(fixture(image=frontend.IMAGES[1]))

    def test_wrong_name_refused(self):
        item = fixture(); item["Name"] = "/other"
        with self.assertRaises(frontend.FrontendBootstrapError):
            frontend.verify_identity(item)

    def test_wrong_project_refused(self):
        item = fixture(); item["Config"]["Labels"]["com.docker.compose.project"] = "other"
        with self.assertRaises(frontend.FrontendBootstrapError):
            frontend.verify_identity(item)

    def test_wrong_service_refused(self):
        item = fixture(); item["Config"]["Labels"]["com.docker.compose.service"] = "backend"
        with self.assertRaises(frontend.FrontendBootstrapError):
            frontend.verify_identity(item)

    def test_wrong_compose_refused(self):
        item = fixture(); item["Config"]["Labels"]["com.docker.compose.project.config_files"] = "/tmp/y.yml"
        with self.assertRaises(frontend.FrontendBootstrapError):
            frontend.verify_identity(item)

    def test_wrong_workdir_refused(self):
        item = fixture(); item["Config"]["Labels"]["com.docker.compose.project.working_dir"] = "/tmp"
        with self.assertRaises(frontend.FrontendBootstrapError):
            frontend.verify_identity(item)

    def test_wrong_image_refused(self):
        item = fixture(image="evil:latest")
        with self.assertRaises(frontend.FrontendBootstrapError):
            frontend.verify_identity(item)

    def test_wrong_network_refused(self):
        item = fixture(); item["NetworkSettings"]["Networks"] = {"other": {}}
        with self.assertRaises(frontend.FrontendBootstrapError):
            frontend.verify_identity(item)

    def test_vault_network_refused(self):
        item = fixture(); item["NetworkSettings"]["Networks"]["transcendence-vault-dev_vault_private"] = {}
        with self.assertRaises(frontend.FrontendBootstrapError):
            frontend.verify_identity(item)

    def test_privileged_refused(self):
        item = fixture(); item["HostConfig"]["Privileged"] = True
        with self.assertRaises(frontend.FrontendBootstrapError):
            frontend.verify_identity(item)

    def test_published_ports_refused(self):
        item = fixture(); item["HostConfig"]["PortBindings"] = {"5173/tcp": [{"HostPort": "5173"}]}
        with self.assertRaises(frontend.FrontendBootstrapError):
            frontend.verify_identity(item)

    def test_unexpected_mount_refused(self):
        item = fixture(); item["Mounts"] = [{"Destination": "/etc/secrets", "RW": False}]
        with self.assertRaises(frontend.FrontendBootstrapError):
            frontend.verify_identity(item)

    def test_backend_url_required(self):
        item = fixture(); item["Config"]["Env"].remove("BACKEND_URL=http://backend:3000")
        with self.assertRaises(frontend.FrontendBootstrapError):
            frontend.verify_identity(item)

    def test_secret_environment_refused(self):
        for var in ("DATABASE_URL=secret", "DATABASE_URL_FILE=/secret", "VAULT_TOKEN=secret",
                    "POSTGRES_PASSWORD=secret", "PGPASSWORD=secret"):
            with self.subTest(var=var.split("=")[0]):
                item = fixture(); item["Config"]["Env"].append(var)
                with self.assertRaises(frontend.FrontendBootstrapError):
                    frontend.verify_identity(item)

    def test_nondict_inspection_refused(self):
        with self.assertRaises(frontend.FrontendBootstrapError):
            frontend.verify_identity([])

    def test_ready_checks_running_state(self):
        self.assertTrue(frontend.ready(fixture()))
        self.assertFalse(frontend.ready(fixture("exited")))

    def test_http_probes_html_entry_and_backend_proxy(self):
        self.assertIn('id="root"', frontend.FRONTEND_HTTP)
        self.assertIn("/src/main.tsx", frontend.FRONTEND_HTTP)
        self.assertIn("/api/v1/health", frontend.FRONTEND_HTTP)
        self.assertIn("database", frontend.FRONTEND_HTTP)
        self.assertNotIn("console.log", frontend.FRONTEND_HTTP)
        with patch.object(frontend.support, "run") as call:
            frontend.probe_http()
            self.assertEqual(call.call_args.args[0][:4], ["docker", "exec", frontend.NAME, "node"])

    def test_plan_missing_readonly(self):
        with patch.object(frontend, "container", return_value=None), \
             patch.object(frontend, "require_prerequisites") as pre, \
             patch.object(frontend, "start_or_keep") as start:
            self.assertEqual(frontend.main(["plan"]), 0)
            pre.assert_not_called(); start.assert_not_called()

    def test_plan_existing_readonly(self):
        with patch.object(frontend, "container", return_value=fixture()), \
             patch.object(frontend, "require_prerequisites") as pre, \
             patch.object(frontend, "probe_http") as probe:
            self.assertEqual(frontend.main(["plan"]), 0)
            pre.assert_not_called(); probe.assert_not_called()

    def test_check_requires_prerequisites(self):
        with patch.object(frontend.support, "main", return_value=1):
            with self.assertRaises(frontend.FrontendBootstrapError):
                frontend.require_prerequisites()

    def test_check_healthy_is_readonly(self):
        with patch.object(frontend, "require_prerequisites"), \
             patch.object(frontend, "container", return_value=fixture()), \
             patch.object(frontend, "probe_http") as probe, \
             patch.object(frontend, "start_or_keep") as start:
            self.assertEqual(frontend.main(["check"]), 0)
            probe.assert_called_once(); start.assert_not_called()

    def test_check_missing_fails_closed(self):
        with patch.object(frontend, "require_prerequisites"), \
             patch.object(frontend, "container", return_value=None), \
             patch.object(frontend, "probe_http") as probe, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(frontend.main(["check"]), 1)
            probe.assert_not_called()

    def test_check_exited_fails_closed(self):
        with patch.object(frontend, "require_prerequisites"), \
             patch.object(frontend, "container", return_value=fixture("exited")), \
             patch.object(frontend, "probe_http") as probe, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(frontend.main(["check"]), 1)
            probe.assert_not_called()

    def test_existing_running_is_kept(self):
        with patch.object(frontend, "container", return_value=fixture()), \
             patch.object(frontend.support, "run") as run:
            frontend.start_or_keep({})
            run.assert_not_called()

    def test_existing_exited_only_started(self):
        with patch.object(frontend, "container", return_value=fixture("exited")), \
             patch.object(frontend.support, "run") as run:
            frontend.start_or_keep({})
            self.assertEqual(run.call_args.args[0], ["docker", "start", frontend.NAME])

    def test_existing_untrusted_refused(self):
        with patch.object(frontend, "container", return_value=fixture(image="untrusted")), \
             patch.object(frontend.support, "run") as run:
            with self.assertRaises(frontend.FrontendBootstrapError):
                frontend.start_or_keep({})
            run.assert_not_called()

    def test_new_container_compose_no_deps_no_recreate(self):
        with patch.object(frontend, "container", side_effect=[None, fixture()]), \
             patch.object(frontend.support, "run") as run:
            frontend.start_or_keep({"PATH": "/usr/bin"})
            args = run.call_args.args[0]
            self.assertIn("--no-deps", args)
            self.assertIn("--no-recreate", args)
            self.assertIn("--build", args)
            self.assertNotIn("--profile", args)
            self.assertEqual(args[-1], "frontend")

    def test_new_untrusted_container_rejected(self):
        with patch.object(frontend, "container", side_effect=[None, fixture(image="untrusted")]), \
             patch.object(frontend.support, "run"):
            with self.assertRaises(frontend.FrontendBootstrapError):
                frontend.start_or_keep({})

    def test_wait_running_probes_http(self):
        with patch.object(frontend, "container", return_value=fixture()), \
             patch.object(frontend, "probe_http") as probe:
            frontend.wait_frontend(attempts=1)
            probe.assert_called_once()

    def test_wait_retries_unready_http(self):
        with patch.object(frontend, "container", return_value=fixture()), \
             patch.object(frontend, "probe_http", side_effect=[frontend.support.SupportServicesError("unready"), None]) as probe, \
             patch.object(frontend.time, "sleep") as sleep:
            frontend.wait_frontend(attempts=2)
            self.assertEqual(probe.call_count, 2)
            sleep.assert_called_once()

    def test_wait_timeout_no_removal(self):
        with patch.object(frontend, "container", return_value=fixture()), \
             patch.object(frontend, "probe_http", side_effect=frontend.support.SupportServicesError("no")), \
             patch.object(frontend.time, "sleep"):
            with self.assertRaises(frontend.FrontendBootstrapError):
                frontend.wait_frontend(attempts=2)

    def test_wait_exited_fails(self):
        with patch.object(frontend, "container", return_value=fixture("exited")), \
             patch.object(frontend, "probe_http") as probe:
            with self.assertRaises(frontend.FrontendBootstrapError):
                frontend.wait_frontend(attempts=1)
            probe.assert_not_called()

    def test_apply_prerequisites_before_start(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "lock"; p.touch()
            with patch.object(frontend.pg, "LOCK", p), \
                 patch.object(frontend, "require_prerequisites", side_effect=frontend.FrontendBootstrapError("no")), \
                 patch.object(frontend, "start_or_keep") as start, \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(frontend.main(["apply"]), 1)
                start.assert_not_called()

    def test_apply_order_and_env(self):
        actions = []
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "lock"; p.touch()
            with patch.object(frontend.pg, "LOCK", p), \
                 patch.object(frontend, "require_prerequisites", side_effect=lambda: actions.append("pre")), \
                 patch.object(frontend, "container", return_value=None), \
                 patch.object(frontend.agent, "safe_env", return_value={"SAFE": "1"}), \
                 patch.object(frontend, "start_or_keep", side_effect=lambda env: actions.append(env)), \
                 patch.object(frontend, "wait_frontend", side_effect=lambda: actions.append("wait")):
                self.assertEqual(frontend.main(["apply"]), 0)
        self.assertEqual(actions, ["pre", {"SAFE": "1"}, "wait"])

    def test_apply_untrusted_existing_refused_before_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "lock"; p.touch()
            with patch.object(frontend.pg, "LOCK", p), \
                 patch.object(frontend, "require_prerequisites"), \
                 patch.object(frontend, "container", return_value=fixture(image="untrusted")), \
                 patch.object(frontend, "start_or_keep") as start, \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(frontend.main(["apply"]), 1)
                start.assert_not_called()

    def test_errors_redact_subprocess_output(self):
        token = b"VAULT_TOKEN=secret-string"
        result = SimpleNamespace(returncode=42, stdout=token, stderr=token)
        with patch.object(frontend.support.subprocess, "run", return_value=result):
            with self.assertRaises(frontend.support.SupportServicesError) as exc:
                frontend.support.run(["docker", "ps"])
            self.assertNotIn("secret-string", str(exc.exception))

    def test_errors_redact_timeout(self):
        with patch.object(frontend.support.subprocess, "run", side_effect=subprocess.TimeoutExpired(
            cmd=["docker"], timeout=1, output=b"secret-string"
        )):
            with self.assertRaises(frontend.support.SupportServicesError) as exc:
                frontend.support.run(["docker", "ps"])
            self.assertNotIn("secret-string", str(exc.exception))


if __name__ == "__main__":
    unittest.main()
