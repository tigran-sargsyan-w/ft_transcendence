"""Offline Phase 5C tests: no Docker and no user resources changed."""

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bootstrap_support_services as services


def fixture(service, status="running", health="healthy"):
    env = ["PATH=/usr/bin"]
    mounts = []
    if service == "docker-proxy":
        env.extend(["POST=0", "CONTAINERS=1", "NETWORKS=1", "VOLUMES=1",
                    "EVENTS=1", "PING=1", "VERSION=1"])
        mounts.append({"Destination": "/var/run/docker.sock", "Type": "bind",
                       "Source": "/var/run/docker.sock", "RW": False})
    elif service == "collector":
        env.extend(["PORT=3001", "DOCKER_API_URL=http://docker-proxy:2375",
                    "ENVIRONMENT_ID=env_local_compose"])
    return {
        "Name": "/" + services.name(service),
        "Config": {
            "Image": services.IMAGES[service][0],
            "Labels": {
                "com.docker.compose.project": services.PROJECT,
                "com.docker.compose.service": service,
                "com.docker.compose.project.config_files": str(services.pg.COMPOSE_FILE),
                "com.docker.compose.project.working_dir": str(services.pg.ROOT),
            },
            "Env": env,
        },
        "HostConfig": {"Privileged": False, "PortBindings": {}},
        "NetworkSettings": {"Networks": {n: {} for n in services.NETWORKS[service]}},
        "Mounts": mounts,
        "State": {"Running": status == "running", "Status": status,
                  "Health": {"Status": health}},
    }


class SupportServicesTests(unittest.TestCase):
    def test_expected_identities_are_accepted(self):
        for service in services.SERVICES:
            with self.subTest(service=service):
                self.assertIsNotNone(services.verify_identity(service, fixture(service)))

    def test_expected_names_are_exact(self):
        for service in services.SERVICES:
            self.assertEqual(services.name(service), f"{services.PROJECT}-{service}-1")
        with self.assertRaises(services.SupportServicesError):
            services.name("postgres")

    def test_invalid_project_rejected(self):
        item = fixture("collector")
        item["Config"]["Labels"]["com.docker.compose.project"] = "untrusted"
        with self.assertRaises(services.SupportServicesError):
            services.verify_identity("collector", item)

    def test_invalid_compose_file_rejected(self):
        item = fixture("collector")
        item["Config"]["Labels"]["com.docker.compose.project.config_files"] = "/tmp/other.yml"
        with self.assertRaises(services.SupportServicesError):
            services.verify_identity("collector", item)

    def test_invalid_image_rejected(self):
        item = fixture("graph-engine")
        item["Config"]["Image"] = "untrusted:latest"
        with self.assertRaises(services.SupportServicesError):
            services.verify_identity("graph-engine", item)

    def test_incorrect_service_name_rejected(self):
        item = fixture("collector")
        item["Name"] = "/transcendence-app-dev-backend-1"
        with self.assertRaises(services.SupportServicesError):
            services.verify_identity("collector", item)

    def test_extra_vault_network_rejected(self):
        item = fixture("collector")
        item["NetworkSettings"]["Networks"]["transcendence-vault-dev_vault_private"] = {}
        with self.assertRaises(services.SupportServicesError):
            services.verify_identity("collector", item)

    def test_proxy_has_only_private_control_network(self):
        item = fixture("docker-proxy")
        item["NetworkSettings"]["Networks"][services.DEFAULT] = {}
        with self.assertRaises(services.SupportServicesError):
            services.verify_identity("docker-proxy", item)

    def test_privileged_rejected(self):
        for service in services.SERVICES:
            item = fixture(service)
            item["HostConfig"]["Privileged"] = True
            with self.subTest(service=service), self.assertRaises(services.SupportServicesError):
                services.verify_identity(service, item)

    def test_published_ports_rejected(self):
        for service in services.SERVICES:
            item = fixture(service)
            item["HostConfig"]["PortBindings"] = {"3001/tcp": [{"HostPort": "3001"}]}
            with self.subTest(service=service), self.assertRaises(services.SupportServicesError):
                services.verify_identity(service, item)

    def test_docker_socket_rw_refused(self):
        item = fixture("docker-proxy")
        item["Mounts"][0]["RW"] = True
        with self.assertRaises(services.SupportServicesError):
            services.verify_identity("docker-proxy", item)

    def test_docker_socket_unexpected_source_rejected(self):
        item = fixture("docker-proxy")
        item["Mounts"][0]["Source"] = "/tmp/untrusted.sock"
        with self.assertRaises(services.SupportServicesError):
            services.verify_identity("docker-proxy", item)

    def test_docker_socket_extra_mount_refused(self):
        item = fixture("docker-proxy")
        item["Mounts"].append({"Destination": "/root", "Type": "bind"})
        with self.assertRaises(services.SupportServicesError):
            services.verify_identity("docker-proxy", item)

    def test_proxy_post_disabled(self):
        item = fixture("docker-proxy")
        item["Config"]["Env"] = [e if e != "POST=0" else "POST=1"
                                    for e in item["Config"]["Env"]]
        with self.assertRaises(services.SupportServicesError):
            services.verify_identity("docker-proxy", item)

    def test_proxy_get_allowlist_required(self):
        item = fixture("docker-proxy")
        item["Config"]["Env"].remove("EVENTS=1")
        with self.assertRaises(services.SupportServicesError):
            services.verify_identity("docker-proxy", item)

    def test_collector_proxy_endpoint_required(self):
        item = fixture("collector")
        item["Config"]["Env"].remove("DOCKER_API_URL=http://docker-proxy:2375")
        with self.assertRaises(services.SupportServicesError):
            services.verify_identity("collector", item)

    def test_collector_unexpected_volume_refused(self):
        item = fixture("collector")
        item["Mounts"].append({"Destination": "/sensitive"})
        with self.assertRaises(services.SupportServicesError):
            services.verify_identity("collector", item)

    def test_graph_unexpected_volume_refused(self):
        item = fixture("graph-engine")
        item["Mounts"].append({"Destination": "/sensitive"})
        with self.assertRaises(services.SupportServicesError):
            services.verify_identity("graph-engine", item)

    def test_no_credentials_environment(self):
        for service in services.SERVICES:
            for secret in ("VAULT_TOKEN=secret", "DATABASE_URL=postgresql://secret",
                           "POSTGRES_PASSWORD=secret", "DATABASE_URL_FILE=/tmp/s"):
                item = fixture(service)
                item["Config"]["Env"].append(secret)
                with self.subTest(service=service, secret=secret.split('=')[0]), \
                        self.assertRaises(services.SupportServicesError):
                    services.verify_identity(service, item)

    def test_service_config_does_not_inherit_operator_env(self):
        with patch.object(services.agent, "safe_env", return_value={"FT_DEV_TLS_DIR": "/tmp/tls"}) as env:
            result = services.agent.safe_env()
            env.assert_called_once()
            self.assertNotIn("VAULT_TOKEN", result)

    def test_plan_missing_never_starts(self):
        with patch.object(services, "inspect", return_value=None), \
             patch.object(services, "require_prerequisites") as requirements, \
             patch.object(services, "start_or_keep") as start:
            self.assertEqual(services.main(["plan"]), 0)
            requirements.assert_not_called()
            start.assert_not_called()

    def test_plan_existing_verifies_identity(self):
        with patch.object(services, "inspect", side_effect=lambda s: fixture(s)), \
             patch.object(services, "probe_services") as probe, \
             patch.object(services, "require_prerequisites") as requirements:
            self.assertEqual(services.main(["plan"]), 0)
            probe.assert_not_called()
            requirements.assert_not_called()

    def test_check_missing_fails_without_mutation(self):
        with patch.object(services, "require_prerequisites"), \
             patch.object(services, "inspect", return_value=None), \
             patch.object(services, "start_or_keep") as start, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(services.main(["check"]), 1)
            start.assert_not_called()

    def test_check_probes_services_readonly(self):
        with patch.object(services, "require_prerequisites"), \
             patch.object(services, "inspect", side_effect=lambda s: fixture(s)), \
             patch.object(services, "probe_services") as probe:
            self.assertEqual(services.main(["check"]), 0)
            probe.assert_called_once()

    def test_check_unhealthy_graph_rejected(self):
        with patch.object(services, "require_prerequisites"), \
             patch.object(services, "inspect", side_effect=lambda s: fixture(s, health="starting")), \
             patch.object(services, "probe_services") as probe, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(services.main(["check"]), 1)
            probe.assert_not_called()

    def test_check_requires_backend(self):
        with patch.object(services.backend, "main", return_value=1):
            with self.assertRaises(services.SupportServicesError):
                services.require_prerequisites()

    def test_new_containers_use_explicit_nondeps_norecreate(self):
        for service in services.SERVICES:
            with self.subTest(service=service), \
                 patch.object(services, "inspect", side_effect=[None, fixture(service), fixture(service)]), \
                 patch.object(services, "run") as run:
                services.start_or_keep(service, {})
                cmd = run.call_args.args[0]
                self.assertIn("--no-deps", cmd)
                self.assertIn("--no-recreate", cmd)
                self.assertNotIn("--profile", cmd)
                self.assertNotIn("down", cmd)
                self.assertEqual(cmd[-1], service)
                self.assertEqual("--build" in cmd, service != "docker-proxy")

    def test_existing_running_kept(self):
        for service in services.SERVICES:
            with self.subTest(service=service), \
                 patch.object(services, "inspect", side_effect=lambda s: fixture(s)), \
                 patch.object(services, "run") as run:
                services.start_or_keep(service, {})
                run.assert_not_called()

    def test_existing_exited_only_docker_start(self):
        with patch.object(services, "inspect", side_effect=lambda s: fixture(s, status="exited")), \
             patch.object(services, "run") as run:
            # The waiting step cannot actually succeed in offline setup.
            with patch.object(services, "wait_running"):
                services.start_or_keep("collector", {})
            self.assertEqual(run.call_args.args[0], ["docker", "start", services.name("collector")])

    def test_existing_untrusted_container_never_starts(self):
        invalid = fixture("collector")
        invalid["Config"]["Image"] = "untrusted"
        with patch.object(services, "inspect", return_value=invalid), \
             patch.object(services, "run") as run:
            with self.assertRaises(services.SupportServicesError):
                services.start_or_keep("collector", {})
            run.assert_not_called()

    def test_failures_hide_subprocess_output(self):
        result = SimpleNamespace(returncode=1, stdout=b"token-secret", stderr=b"token-secret")
        with patch.object(services.subprocess, "run", return_value=result):
            with self.assertRaises(services.SupportServicesError) as error:
                services.run(["docker", "ps"])
            self.assertNotIn("token-secret", str(error.exception))

    def test_timeouts_hide_subprocess_output(self):
        with patch.object(services.subprocess, "run", side_effect=subprocess.TimeoutExpired(
            cmd=["docker", "ps"], timeout=1, output=b"token-secret"
        )):
            with self.assertRaises(services.SupportServicesError) as error:
                services.run(["docker", "ps"])
            self.assertNotIn("token-secret", str(error.exception))

    def test_readonly_health_probe_no_raw_responses(self):
        self.assertIn("/api/v1/snapshot", services.COLLECTOR_HTTP)
        self.assertIn("schemaVersion", services.COLLECTOR_HTTP)
        self.assertIn("/api/v1/health", services.BACKEND_REACHABILITY)
        self.assertNotIn("console.log", services.COLLECTOR_HTTP)
        with patch.object(services, "run") as run:
            services.probe_services()
            self.assertEqual(run.call_count, 3)
            self.assertEqual(run.call_args_list[0].args[0][1:3],
                             ["exec", services.name("collector")])

    def test_apply_requires_prerequisites_before_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            lock = Path(td) / "lock"; lock.touch()
            with patch.object(services.pg, "LOCK", lock), \
                 patch.object(services, "require_prerequisites", side_effect=services.SupportServicesError("failed")), \
                 patch.object(services, "start_or_keep") as start, \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(services.main(["apply"]), 1)
                start.assert_not_called()

    def test_apply_correct_order(self):
        order = []
        with tempfile.TemporaryDirectory() as td:
            lock = Path(td) / "lock"; lock.touch()
            with patch.object(services.pg, "LOCK", lock), \
                 patch.object(services, "require_prerequisites", side_effect=lambda: order.append("prerequisites")), \
                 patch.object(services, "inspect", return_value=None), \
                 patch.object(services, "start_or_keep", side_effect=lambda s, e: order.append(s)), \
                 patch.object(services, "probe_services", side_effect=lambda: order.append("probes")):
                self.assertEqual(services.main(["apply"]), 0)
        self.assertEqual(order, ["prerequisites", *services.SERVICES, "probes"])

    def test_apply_invalid_existing_container_refuses_before_any_start(self):
        def inspect(service):
            item = fixture(service)
            if service == "graph-engine":
                item["HostConfig"]["Privileged"] = True
            return item
        with tempfile.TemporaryDirectory() as td:
            lock = Path(td) / "lock"; lock.touch()
            with patch.object(services.pg, "LOCK", lock), \
                 patch.object(services, "require_prerequisites"), \
                 patch.object(services, "inspect", side_effect=inspect), \
                 patch.object(services, "start_or_keep") as start, \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(services.main(["apply"]), 1)
                start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
