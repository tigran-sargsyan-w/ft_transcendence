"""Offline Grafana startup tests; no Docker or Vault connection."""

import json
import unittest

from types import SimpleNamespace
from unittest.mock import patch

import bootstrap_grafana as mod


def trusted_grafana():
    mounts = []

    for destination, (kind, source, rw) in mod.EXPECTED_MOUNTS.items():
        mounts.append({
            "Type": kind,
            "Name" if kind == "volume" else "Source": source,
            "Destination": destination,
            "RW": rw,
        })

    return {
        "Name": "/" + mod.NAME,
        "Id": "test-grafana-id",
        "Config": {
            "Image": mod.IMAGE,
            "User": "472:20030",
            "Entrypoint": mod.ENTRYPOINT,
            "Cmd": [mod.STARTUP + "\n"],
            "Labels": {
                "com.docker.compose.project": mod.pg.PROJECT,
                "com.docker.compose.service": mod.SERVICE,
                "com.docker.compose.project.config_files":
                    str(mod.pg.COMPOSE_FILE),
                "com.docker.compose.project.working_dir":
                    str(mod.pg.ROOT),
            },
            "Env": [
                f"{key}={value}"
                for key, value in mod.GRAFANA_ENV.items()
            ],
        },
        "HostConfig": {
            "Privileged": False,
            "PortBindings": {},
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Tmpfs": {"/tmp": ""},
        },
        "NetworkSettings": {
            "Networks": {mod.NETWORK: {}},
        },
        "Mounts": mounts,
        "State": {
            "Running": True,
            "Status": "running",
        },
    }


class GrafanaTests(unittest.TestCase):
    def test_expected_identity(self):
        item = trusted_grafana()
        self.assertIs(mod.verify_identity(item), item)
        self.assertTrue(mod.running(item))

    def test_unsafe_runtime_rejected(self):
        for field, value in (
            ("Privileged", True),
            ("ReadonlyRootfs", False),
            ("CapDrop", []),
            ("CapAdd", ["SYS_ADMIN"]),
            ("PortBindings", {"3000/tcp": [{}]}),
            ("PublishAllPorts", True),
            ("Devices", [{}]),
            ("Tmpfs", {}),
        ):
            with self.subTest(field=field):
                item = trusted_grafana()
                item["HostConfig"][field] = value

                with self.assertRaises(mod.GrafanaBootstrapError):
                    mod.verify_identity(item)

    def test_vault_network_rejected(self):
        item = trusted_grafana()
        item["NetworkSettings"]["Networks"]["vault_private"] = {}

        with self.assertRaises(mod.GrafanaBootstrapError):
            mod.verify_identity(item)

    def test_unexpected_mount_rejected(self):
        item = trusted_grafana()

        item["Mounts"].append({
            "Type": "volume",
            "Name": "grafana_vault_auth",
            "Destination": "/vault/auth",
            "RW": False,
        })

        with self.assertRaises(mod.GrafanaBootstrapError):
            mod.verify_identity(item)

    def test_writable_secret_mount_rejected(self):
        item = trusted_grafana()

        for mount in item["Mounts"]:
            if mount["Destination"] == "/run/secrets/grafana":
                mount["RW"] = True

        with self.assertRaises(mod.GrafanaBootstrapError):
            mod.verify_identity(item)

    def test_environment_security_overrides_rejected(self):
        for value in (
            "GF_AUTH_ANONYMOUS_ENABLED=true",
            "GF_SECURITY_ADMIN_PASSWORD=fake",
            "GF_SECURITY_SECRET_KEY=fake",
            "VAULT_TOKEN=fake",
        ):
            with self.subTest(value=value):
                item = trusted_grafana()
                item["Config"]["Env"].append(value)

                with self.assertRaises(mod.GrafanaBootstrapError):
                    mod.verify_identity(item)

    def test_startup_command_cannot_bypass_secret_validation(self):
        item = trusted_grafana()
        item["Config"]["Cmd"] = ["exec /run.sh"]

        with self.assertRaises(mod.GrafanaBootstrapError):
            mod.verify_identity(item)

    def test_running_grafana_never_recreated(self):
        with patch.object(mod, "container",
                          return_value=trusted_grafana()), \
             patch.object(mod.agent, "run") as run:

            mod.start_or_keep({})

        run.assert_not_called()

    def test_stopped_grafana_started_by_existing_id(self):
        item = trusted_grafana()
        item["State"] = {"Running": False, "Status": "exited"}

        with patch.object(mod, "container", return_value=item), \
             patch.object(mod.agent, "run") as run:

            mod.start_or_keep({})

        self.assertEqual(
            run.call_args.args[0],
            ["docker", "start", item["Id"]],
        )

    def test_missing_grafana_creates_only_target_service(self):
        with patch.object(mod, "container",
                          side_effect=[None, trusted_grafana()]), \
             patch.object(mod.agent, "run"), \
             patch.object(mod.agent, "app_compose") as compose:

            mod.start_or_keep({})

        compose.assert_called_once_with(
            "--profile", "monitoring",
            "up", "-d", "--no-deps", "--no-recreate",
            "--no-build", "--pull", "never",
            "grafana",
        )

    def test_password_is_read_inside_container(self):
        with patch.object(
            mod.agent, "run",
            return_value=SimpleNamespace(
                stdout=b'{"login":"admin"}',
            ),
        ) as run:
            mod.authenticated_api("/api/user")

        command = run.call_args.args[0]

        self.assertEqual(command[:3], ["docker", "exec", mod.NAME])
        self.assertIn("curl -q --config -", mod.AUTH_PROBE)
        self.assertIn("/run/secrets/grafana/admin_password", mod.AUTH_PROBE)
        self.assertNotIn("--user", mod.AUTH_PROBE)
        self.assertNotIn("--header", mod.AUTH_PROBE)
        self.assertNotIn("export password", mod.AUTH_PROBE)

    def test_anonymous_access_must_return_401(self):
        with patch.object(
            mod.agent, "run",
            return_value=SimpleNamespace(stdout=b"200"),
        ):
            with self.assertRaises(mod.GrafanaBootstrapError):
                mod.verify_anonymous_access()

    def test_unexpected_api_path_rejected(self):
        with patch.object(mod.agent, "run") as run:
            with self.assertRaises(mod.GrafanaBootstrapError):
                mod.authenticated_api("/api/admin/settings")

        run.assert_not_called()

    def test_invalid_health_response_rejected(self):
        with patch.object(mod, "request_json", return_value={
            "database": "failed",
            "version": "13.3.1",
        }):
            with self.assertRaises(mod.GrafanaBootstrapError):
                mod.probe_health()

    def test_expected_provisioning(self):
        responses = [
            {"login": "admin"},
            {
                "uid": "ft-prometheus",
                "type": "prometheus",
                "url": "http://prometheus:9090",
                "access": "proxy",
                "isDefault": True,
                "readOnly": True,
            },
            {
                "dashboard": {
                    "uid": "ft-demo-lab",
                    "panels": [{}] * 5,
                },
                "meta": {"provisioned": True},
            },
            {
                "dashboard": {
                    "uid": "ft-monitoring-pipeline",
                    "panels": [{}] * 6,
                },
                "meta": {"provisioned": True},
            },
        ]

        with patch.object(
            mod, "authenticated_api",
            side_effect=responses,
        ):
            mod.verify_provisioning()

    def test_datasource_mismatch_rejected(self):
        with patch.object(mod, "authenticated_api", side_effect=[
            {"login": "admin"},
            {"url": "http://unexpected:9090"},
        ]):
            with self.assertRaises(mod.GrafanaBootstrapError):
                mod.verify_provisioning()

    def test_check_never_starts_or_recreates(self):
        with patch.object(mod, "require_prerequisites"), \
             patch.object(mod, "container",
                          return_value=trusted_grafana()), \
             patch.object(mod, "probe_http") as probe, \
             patch.object(mod, "start_or_keep") as start:

            mod.check()

        probe.assert_called_once()
        start.assert_not_called()

    def test_plan_never_reads_credentials(self):
        with patch.object(mod, "container", return_value=None), \
             patch.object(mod, "require_prerequisites") as prerequisites, \
             patch.object(mod, "apply") as apply:

            self.assertEqual(mod.main(["plan"]), 0)

        prerequisites.assert_not_called()
        apply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
