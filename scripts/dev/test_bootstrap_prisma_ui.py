"""Offline lifecycle tests; no Docker, database or secret mutations."""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, call

import bootstrap_prisma_ui as ui


def fixture(service, *, running=True):
    gateway = service == "gateway"
    ports = {"5556/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5555"}]} if gateway else {}
    mounts = ([{"Type": "bind", "Source": str(ui.GATEWAY_CONFIG),
                "Destination": "/usr/local/etc/haproxy/haproxy.cfg", "RW": False},
               {"Type": "bind", "Source": str(ui.ROOT / "infra/nginx/certs"),
                "Destination": "/etc/haproxy/certs", "RW": False}] if gateway else
              [{"Type": "volume", "Name": ui.agent.RENDERED_VOLUME,
                "Destination": "/run/secrets/backend", "RW": False}])
    return {
        "Name": f"/{ui.PROJECT}-{service}-1", "Id": service + "-id", "Image": "backend-id",
        "Config": {"Labels": {"com.docker.compose." + k: v for k, v in {
            "project": ui.PROJECT, "service": service,
            "project.config_files": str(ui.COMPOSE), "project.working_dir": str(ui.COMPOSE.parent)}.items()},
            "Image": ui.IMAGE if gateway else ui.backend.EXPECTED_IMAGE,
            "User": "99:99" if gateway else "10001:20000",
            "Env": ["DEV_PRISMA_UI_PORT=5555"] if gateway else ["DATABASE_URL_FILE=/run/secrets/backend/database_url"],
            "Cmd": [] if gateway else ["/app/node_modules/.bin/prisma", "studio", "--port", "5555", "--browser", "none"]},
        "HostConfig": {"Privileged": False, "ReadonlyRootfs": True, "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"], "RestartPolicy": {"Name": "no"},
            "GroupAdd": ["43043"] if gateway else [], "PortBindings": ports,
            "NetworkMode": ui.NETWORK if gateway else "container:gateway-id"},
        "NetworkSettings": {"Networks": {ui.NETWORK: {}} if gateway else {}, "Ports": ports},
        "Mounts": mounts, "State": {"Running": running, "Status": "running" if running else "exited"},
    }


class PrismaLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.items = {service: fixture(service) for service in ui.SERVICES}
        self.empty = dict.fromkeys(ui.SERVICES)
        self.stopped = {service: fixture(service, running=False) for service in ui.SERVICES}
        self.addCleanup(patch.stopall)
        self.local = patch.object(ui, "local_docker").start()
        self.inventory = patch.object(ui, "inventory", return_value=self.items).start()
        self.backend_image = patch.object(ui, "backend_image", return_value=("backend", "backend-id")).start()
        self.up = patch.object(ui, "compose_up").start()
        self.probe = patch.object(ui, "probe").start()
        self.run = patch.object(ui, "run").start()

    def test_enable_running_keeps_both_containers(self):
        self.assertTrue(ui.lifecycle("enable", 5555))
        self.up.assert_not_called()
        self.run.assert_not_called()
        self.probe.assert_called_once_with(5555, attempts=15)

    def test_enable_absent_creates_only_reviewed_pair(self):
        self.inventory.side_effect = [self.empty, self.items]
        self.assertTrue(ui.lifecycle("enable", 5555))
        self.up.assert_called_once_with(5555, "backend")
        self.run.assert_not_called()

    def test_enable_partial_refuses_mutation(self):
        self.items["studio"] = None
        with self.assertRaises(ui.StudioError):
            ui.lifecycle("enable", 5555)
        self.up.assert_not_called()
        self.run.assert_not_called()

    def test_enable_old_image_refuses_mutation(self):
        self.items["studio"]["Image"] = "old"
        with self.assertRaises(ui.StudioError):
            ui.lifecycle("enable", 5555)
        self.up.assert_not_called()

    def test_enable_stopped_starts_gateway_then_studio(self):
        self.inventory.side_effect = [self.stopped, self.items]
        self.assertTrue(ui.lifecycle("enable", 5555))
        self.assertEqual(self.run.call_args_list, [
            call("docker", "start", "gateway-id"), call("docker", "start", "studio-id")])
        self.up.assert_not_called()
        self.probe.assert_called_once_with(5555, attempts=15)

    def test_enable_stopped_old_image_requires_manual_recreation(self):
        self.stopped["studio"]["Image"] = "old"
        self.inventory.return_value = self.stopped
        with self.assertRaisesRegex(ui.StudioError, "manually remove"):
            ui.lifecycle("enable", 5555)
        self.run.assert_not_called()
        self.up.assert_not_called()

    def test_enable_rejects_changed_container_id(self):
        self.items["studio"]["Id"] = "replacement-id"
        self.inventory.side_effect = [self.stopped, self.items]
        with self.assertRaisesRegex(ui.StudioError, "identity changed"):
            ui.lifecycle("enable", 5555)
        self.probe.assert_not_called()

    def test_disable_order_and_retains_both_containers(self):
        self.inventory.side_effect = [self.items, self.stopped]
        self.assertFalse(ui.lifecycle("disable", 5555))
        self.assertEqual(self.run.call_args_list, [
            call("docker", "stop", "--time", "10", "studio-id"),
            call("docker", "stop", "--time", "10", "gateway-id")])
        self.backend_image.assert_not_called()

    def test_disable_stopped_is_noop(self):
        self.inventory.return_value = self.stopped
        self.assertFalse(ui.lifecycle("disable", 5555))
        self.run.assert_not_called()

    def test_disable_rejects_changed_container_id(self):
        self.stopped["studio"]["Id"] = "replacement-id"
        self.inventory.side_effect = [self.items, self.stopped]
        with self.assertRaisesRegex(ui.StudioError, "retained stopped"):
            ui.lifecycle("disable", 5555)

    def test_disable_rejects_container_still_running(self):
        self.inventory.return_value = self.items
        with self.assertRaisesRegex(ui.StudioError, "retained stopped"):
            ui.lifecycle("disable", 5555)

    def test_disable_absent_is_noop(self):
        self.inventory.return_value = self.empty
        self.assertFalse(ui.lifecycle("disable", 5555))
        self.run.assert_not_called()

    def test_status_absent_without_core_dependencies(self):
        self.inventory.return_value = self.empty
        self.assertFalse(ui.lifecycle("status", 5555))
        self.backend_image.assert_not_called()
        self.probe.assert_not_called()
        self.run.assert_not_called()

    def test_status_running_read_only(self):
        self.assertTrue(ui.lifecycle("status", 5555))
        self.probe.assert_called_once_with(5555)
        self.run.assert_not_called()

    def test_status_stopped_is_disabled(self):
        self.inventory.return_value = self.stopped
        self.assertFalse(ui.lifecycle("status", 5555))
        self.probe.assert_not_called()
        self.run.assert_not_called()

    def test_status_mixed_running_and_stopped_refused(self):
        self.items["studio"] = self.stopped["studio"]
        with self.assertRaises(ui.StudioError):
            ui.lifecycle("status", 5555)
        self.run.assert_not_called()

    def test_enable_mixed_running_and_stopped_refuses_mutation(self):
        self.items["studio"] = self.stopped["studio"]
        with self.assertRaises(ui.StudioError):
            ui.lifecycle("enable", 5555)
        self.up.assert_not_called()
        self.run.assert_not_called()

    def test_identity_failure_prevents_disable(self):
        self.inventory.side_effect = ui.StudioError("foreign container")
        with self.assertRaises(ui.StudioError):
            ui.lifecycle("disable", 5555)
        self.run.assert_not_called()


class PrismaContractTests(unittest.TestCase):
    def test_reviewed_sources(self):
        ui.verify_source()

    def test_identity_accepts_expected_pair(self):
        for service in ui.SERVICES:
            ui.verify_identity(service, fixture(service), 5555)

    def test_identity_rejects_foreign_project(self):
        item = fixture("gateway")
        item["Config"]["Labels"]["com.docker.compose.project"] = "foreign"
        with self.assertRaises(ui.StudioError):
            ui.verify_identity("gateway", item, 5555)

    def test_identity_rejects_public_port(self):
        item = fixture("gateway")
        item["HostConfig"]["PortBindings"]["5556/tcp"][0]["HostIp"] = "0.0.0.0"
        with self.assertRaises(ui.StudioError):
            ui.verify_identity("gateway", item, 5555)

    def test_identity_rejects_writable_secret(self):
        item = fixture("studio")
        item["Mounts"][0]["RW"] = True
        with self.assertRaises(ui.StudioError):
            ui.verify_identity("studio", item, 5555)

    def test_inventory_rejects_namespace_drift(self):
        gateway, studio = fixture("gateway"), fixture("studio")
        studio["HostConfig"]["NetworkMode"] = "container:other-id"
        with patch.object(ui.pg, "exact_container", side_effect=[gateway, studio]), self.assertRaises(ui.StudioError):
            ui.inventory(5555)

    def test_compose_never_inherits_credentials_or_builds(self):
        with patch.dict(os.environ, {"DATABASE_URL": "secret", "VAULT_TOKEN": "secret", "DEV_PRISMA_UI_PORT": "9999"}), \
                patch.object(ui, "run") as run:
            ui.compose_up(5555, "backend")
        args, kwargs = run.call_args
        self.assertEqual(args[-2:], ("gateway", "studio"))
        self.assertIn("--no-recreate", args)
        self.assertIn("--no-build", args)
        self.assertNotIn("DATABASE_URL", kwargs["env"])
        self.assertNotIn("VAULT_TOKEN", kwargs["env"])
        self.assertEqual(kwargs["env"]["DEV_PRISMA_UI_PORT"], "5555")

    def test_probe_explicit_ca_and_no_proxy(self):
        with patch.object(ui.certificates, "curl_ca_args", return_value=["--cacert", "/ca.pem"]), \
                patch.object(ui, "run", return_value="200") as run:
            ui.probe(5555)
        self.assertIn("--cacert", run.call_args.args)
        self.assertIn("--noproxy", run.call_args.args)
        self.assertNotIn("-k", run.call_args.args)
        self.assertEqual(run.call_args.args[1], "-q")

    def test_direct_entrypoint_without_pythonpath(self):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        result = subprocess.run([sys.executable, str(ui.ROOT / "scripts/dev/bootstrap_prisma_ui.py"), "--help"],
                                cwd=ui.ROOT, env=env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())


if __name__ == "__main__":
    unittest.main()
