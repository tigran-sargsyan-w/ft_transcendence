"""Phase 5E Nginx HTTPS bootstrap regressions; purely offline mocks."""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

import bootstrap_nginx as nginx


def fixture(status="running"):
    return {
        "Name": "/" + nginx.NAME,
        "Config": {
            "Labels": {
                "com.docker.compose.project": nginx.pg.PROJECT,
                "com.docker.compose.service": nginx.SERVICE,
                "com.docker.compose.project.config_files": str(nginx.pg.COMPOSE_FILE),
                "com.docker.compose.project.working_dir": str(nginx.pg.ROOT),
            },
            "Image": nginx.IMAGE,
            "Env": ["PATH=/usr/sbin:/usr/bin"],
        },
        "HostConfig": {
            "Privileged": False,
            "PortBindings": nginx.PORTS,
            "RestartPolicy": {"Name": "unless-stopped"},
        },
        "Mounts": [
            {"Destination": "/etc/nginx/conf.d/default.conf", "Type": "bind",
             "Source": str(nginx.CONFIG), "RW": False},
            {"Destination": "/etc/nginx/certs", "Type": "bind",
             "Source": str(nginx.CERTS), "RW": False},
        ],
        "NetworkSettings": {"Networks": {nginx.DEFAULT_NETWORK: {}}},
        "State": {"Running": status == "running", "Status": status},
    }


def responses():
    return [
        b"",
        b'<div id="root"></div><script src="/src/main.tsx"></script>\n200',
        b"/* Vite */ createRoot(document.querySelector('#root'))\n200",
        b'{"data":{"status":"ok","database":"ok"}}\n200',
        b'308\nhttps://transcendence.test:8443/',
    ]


class NginxTests(unittest.TestCase):
    def test_valid_container(self):
        nginx.verify_identity(fixture())

    def test_rejects_non_dict(self):
        with self.assertRaises(nginx.NginxBootstrapError):
            nginx.verify_identity([])

    def test_rejects_wrong_name(self):
        item = fixture(); item["Name"] = "/elsewhere"
        with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_identity(item)

    def test_rejects_wrong_project(self):
        item = fixture(); item["Config"]["Labels"]["com.docker.compose.project"] = "fake"
        with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_identity(item)

    def test_rejects_wrong_service(self):
        item = fixture(); item["Config"]["Labels"]["com.docker.compose.service"] = "fake"
        with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_identity(item)

    def test_rejects_wrong_compose(self):
        item = fixture(); item["Config"]["Labels"]["com.docker.compose.project.config_files"] = "/tmp/bad.yml"
        with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_identity(item)

    def test_rejects_wrong_workdir(self):
        item = fixture(); item["Config"]["Labels"]["com.docker.compose.project.working_dir"] = "/tmp"
        with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_identity(item)

    def test_rejects_wrong_image(self):
        item = fixture(); item["Config"]["Image"] = "nginx:latest"
        with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_identity(item)

    def test_rejects_extra_network(self):
        item = fixture(); item["NetworkSettings"]["Networks"]["transcendence-vault-dev_vault_private"] = {}
        with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_identity(item)

    def test_rejects_no_network(self):
        item = fixture(); item["NetworkSettings"]["Networks"] = {}
        with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_identity(item)

    def test_rejects_privileged(self):
        item = fixture(); item["HostConfig"]["Privileged"] = True
        with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_identity(item)

    def test_rejects_public_http_binding(self):
        item = fixture(); item["HostConfig"]["PortBindings"] = {
            **nginx.PORTS, "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]}
        with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_identity(item)

    def test_rejects_public_https_binding(self):
        item = fixture(); item["HostConfig"]["PortBindings"] = {
            **nginx.PORTS, "443/tcp": [{"HostIp": "::", "HostPort": "8443"}]}
        with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_identity(item)

    def test_rejects_extra_port(self):
        item = fixture(); item["HostConfig"]["PortBindings"] = {
            **nginx.PORTS, "5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5432"}]}
        with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_identity(item)

    def test_rejects_wrong_https_port(self):
        item = fixture(); item["HostConfig"]["PortBindings"] = {
            **nginx.PORTS, "443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "443"}]}
        with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_identity(item)

    def test_rejects_wrong_restart_policy(self):
        item = fixture(); item["HostConfig"]["RestartPolicy"] = {"Name": "always"}
        with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_identity(item)

    def test_rejects_rw_certificates(self):
        item = fixture(); item["Mounts"][1]["RW"] = True
        with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_identity(item)

    def test_rejects_wrong_certificate_source(self):
        item = fixture(); item["Mounts"][1]["Source"] = "/tmp/rogue"
        with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_identity(item)

    def test_rejects_rw_config(self):
        item = fixture(); item["Mounts"][0]["RW"] = True
        with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_identity(item)

    def test_rejects_wrong_config_source(self):
        item = fixture(); item["Mounts"][0]["Source"] = "/tmp/default.conf"
        with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_identity(item)

    def test_rejects_extra_mount(self):
        item = fixture(); item["Mounts"].append({"Destination": "/vault"})
        with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_identity(item)

    def test_rejects_credentials_in_env(self):
        for entry in nginx.FORBIDDEN_ENV:
            item = fixture(); item["Config"]["Env"].append(entry + "super-secret")
            with self.subTest(variable=entry.split("=")[0]), self.assertRaises(nginx.NginxBootstrapError):
                nginx.verify_identity(item)

    def test_running_status(self):
        self.assertTrue(nginx.running(fixture()))
        self.assertFalse(nginx.running(fixture("exited")))

    def test_tls_checks_existing_cert_and_trust_only(self):
        with patch.object(nginx, "run") as cmd:
            nginx.check_local_tls()
            self.assertEqual(cmd.call_count, 2)
            self.assertIn("scripts.dev.certificates", cmd.call_args_list[0].args[0])
            self.assertIn("scripts.dev.ca_trust", cmd.call_args_list[1].args[0])
            self.assertNotIn("setup", str(cmd.call_args_list))

    def test_https_probes_and_redirect(self):
        with patch.object(nginx, "run", side_effect=responses()) as cmd:
            nginx.verify_routes()
            self.assertEqual(cmd.call_count, 5)
            self.assertEqual(cmd.call_args_list[0].args[0], ["docker", "exec", nginx.NAME, "nginx", "-t"])
            for args in [x.args[0] for x in cmd.call_args_list[1:]]:
                self.assertEqual(args[:2], ["curl", "-q"])
                self.assertNotIn("--insecure", args)
                self.assertNotIn("-k", args)
                self.assertIn("--noproxy", args)
                self.assertIn("--resolve", args)

    def test_rejects_bad_html(self):
        samples = responses(); samples[1] = b"404\n404"
        with patch.object(nginx, "run", side_effect=samples):
            with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_routes()

    def test_rejects_http_error_even_if_body_looks_valid(self):
        samples = responses(); samples[1] = samples[1].replace(b"\n200", b"\n503")
        with patch.object(nginx, "run", side_effect=samples):
            with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_routes()

    def test_rejects_missing_status_code(self):
        samples = responses(); samples[1] = b"no status code"
        with patch.object(nginx, "run", side_effect=samples):
            with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_routes()

    def test_rejects_bad_vite_module(self):
        samples = responses(); samples[2] = b"not compiled\n200"
        with patch.object(nginx, "run", side_effect=samples):
            with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_routes()

    def test_rejects_bad_api(self):
        samples = responses(); samples[3] = b'{"data":{"status":"pending","database":"ok"}}\n200'
        with patch.object(nginx, "run", side_effect=samples):
            with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_routes()

    def test_rejects_nonjson_api(self):
        samples = responses(); samples[3] = b"<html>error</html>\n200"
        with patch.object(nginx, "run", side_effect=samples):
            with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_routes()

    def test_rejects_bad_redirect(self):
        samples = responses(); samples[4] = b"301\nhttps://transcendence.test:8443/"
        with patch.object(nginx, "run", side_effect=samples):
            with self.assertRaises(nginx.NginxBootstrapError): nginx.verify_routes()

    def test_plan_missing_readonly(self):
        with patch.object(nginx, "container", return_value=None), \
             patch.object(nginx, "require_prerequisites") as pre, \
             patch.object(nginx, "start_or_keep") as start:
            self.assertEqual(nginx.main(["plan"]), 0)
            pre.assert_not_called(); start.assert_not_called()

    def test_plan_existing_readonly(self):
        with patch.object(nginx, "container", return_value=fixture()), \
             patch.object(nginx, "verify_routes") as probe, \
             patch.object(nginx, "require_prerequisites") as pre:
            self.assertEqual(nginx.main(["plan"]), 0)
            probe.assert_not_called(); pre.assert_not_called()

    def test_check_missing_refused(self):
        with patch.object(nginx, "require_prerequisites"), \
             patch.object(nginx, "container", return_value=None), \
             patch.object(nginx, "start_or_keep") as start, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(nginx.main(["check"]), 1)
            start.assert_not_called()

    def test_check_is_readonly(self):
        with patch.object(nginx, "require_prerequisites"), \
             patch.object(nginx, "container", return_value=fixture()), \
             patch.object(nginx, "verify_routes") as probe, \
             patch.object(nginx, "start_or_keep") as start:
            self.assertEqual(nginx.main(["check"]), 0)
            probe.assert_called_once(); start.assert_not_called()

    def test_check_exited_refused(self):
        with patch.object(nginx, "require_prerequisites"), \
             patch.object(nginx, "container", return_value=fixture("exited")), \
             patch.object(nginx, "verify_routes") as probe, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(nginx.main(["check"]), 1)
            probe.assert_not_called()

    def test_missing_frontend_refused(self):
        with patch.object(nginx.frontend, "main", return_value=1), \
             patch.object(nginx, "check_local_tls") as tls:
            with self.assertRaises(nginx.NginxBootstrapError): nginx.require_prerequisites()
            tls.assert_not_called()

    def test_missing_local_cert_refused(self):
        with patch.object(nginx.frontend, "main", return_value=0), \
             patch.object(nginx, "check_local_tls", side_effect=nginx.NginxBootstrapError("no cert")):
            with self.assertRaises(nginx.NginxBootstrapError): nginx.require_prerequisites()

    def test_existing_running_kept(self):
        with patch.object(nginx, "container", return_value=fixture()), \
             patch.object(nginx, "run") as cmd:
            nginx.start_or_keep({})
            cmd.assert_not_called()

    def test_existing_stopped_started_only(self):
        with patch.object(nginx, "container", return_value=fixture("exited")), \
             patch.object(nginx, "run") as cmd:
            nginx.start_or_keep({})
            self.assertEqual(cmd.call_args.args[0], ["docker", "start", nginx.NAME])

    def test_missing_creates_only_nginx_without_deps(self):
        with patch.object(nginx, "container", side_effect=[None, fixture()]), \
             patch.object(nginx, "run") as cmd:
            nginx.start_or_keep({"PATH": "/usr/bin"})
            args = cmd.call_args.args[0]
            self.assertEqual(args[-1], "nginx")
            self.assertIn("--no-deps", args)
            self.assertIn("--no-recreate", args)
            self.assertNotIn("--build", args)
            self.assertNotIn("down", args)

    def test_untrusted_existing_refused_before_start(self):
        bad = fixture(); bad["HostConfig"]["Privileged"] = True
        with patch.object(nginx, "container", return_value=bad), \
             patch.object(nginx, "run") as cmd:
            with self.assertRaises(nginx.NginxBootstrapError): nginx.start_or_keep({})
            cmd.assert_not_called()

    def test_wait_probes_ready(self):
        with patch.object(nginx, "container", return_value=fixture()), \
             patch.object(nginx, "verify_routes") as probe:
            nginx.wait_nginx(attempts=1)
            probe.assert_called_once()

    def test_wait_retries_http_until_ready(self):
        with patch.object(nginx, "container", return_value=fixture()), \
             patch.object(nginx, "verify_routes", side_effect=[nginx.NginxBootstrapError("not yet"), None]) as probe, \
             patch.object(nginx.time, "sleep") as sleep:
            nginx.wait_nginx(attempts=2)
            self.assertEqual(probe.call_count, 2)
            sleep.assert_called_once()

    def test_wait_exited_refused(self):
        with patch.object(nginx, "container", return_value=fixture("exited")), \
             patch.object(nginx, "verify_routes") as probe:
            with self.assertRaises(nginx.NginxBootstrapError): nginx.wait_nginx(attempts=1)
            probe.assert_not_called()

    def test_apply_orders_preflight_start_health(self):
        order = []
        with tempfile.TemporaryDirectory() as td:
            lock = Path(td) / "lock"; lock.touch()
            with patch.object(nginx.pg, "LOCK", lock), \
                 patch.object(nginx, "require_prerequisites", side_effect=lambda: order.append("pre")), \
                 patch.object(nginx, "container", return_value=None), \
                 patch.object(nginx, "start_or_keep", side_effect=lambda _: order.append("start")), \
                 patch.object(nginx, "wait_nginx", side_effect=lambda: order.append("probe")):
                self.assertEqual(nginx.main(["apply"]), 0)
        self.assertEqual(order, ["pre", "start", "probe"])

    def test_apply_checks_existing_before_start(self):
        bad = fixture(); bad["Mounts"][1]["RW"] = True
        with tempfile.TemporaryDirectory() as td:
            lock = Path(td) / "lock"; lock.touch()
            with patch.object(nginx.pg, "LOCK", lock), \
                 patch.object(nginx, "require_prerequisites"), \
                 patch.object(nginx, "container", return_value=bad), \
                 patch.object(nginx, "start_or_keep") as start, \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(nginx.main(["apply"]), 1)
                start.assert_not_called()

    def test_subprocess_redacts_failure(self):
        result = SimpleNamespace(returncode=10, stdout=b"SECRET", stderr=b"SECRET")
        with patch.object(nginx.subprocess, "run", return_value=result):
            with self.assertRaises(nginx.NginxBootstrapError) as error:
                nginx.run(["docker", "ps"])
            self.assertNotIn("SECRET", str(error.exception))

    def test_subprocess_redacts_timeout(self):
        with patch.object(nginx.subprocess, "run", side_effect=subprocess.TimeoutExpired(
            cmd=["curl", "-q"], timeout=1, output=b"SECRET"
        )):
            with self.assertRaises(nginx.NginxBootstrapError) as error:
                nginx.run(["curl", "-q"])
            self.assertNotIn("SECRET", str(error.exception))


if __name__ == "__main__":
    unittest.main()
