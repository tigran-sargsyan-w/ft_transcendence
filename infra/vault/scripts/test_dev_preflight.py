#!/usr/bin/env python3
"""Offline, non-destructive tests of the #115 development preflight."""

import importlib.util
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).with_name("dev-preflight.py")
SPEC = importlib.util.spec_from_file_location("dev_preflight", MODULE)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        self.state = self.base / "state"
        self.state.mkdir()
        (self.state / "tls").mkdir()
        (self.state / "full-stack.override.yml").write_text("services: {}\\n")
        for name in ("app.override.yml", "vault.override.yml"):
            (self.state / name).write_text("services: {}\n")
        for name in ("ca.crt", "vault.crt", "vault.key"):
            (self.state / "tls" / name).write_text("fake certificate fixture")
        self.pre = preflight.Preflight(self.root, self.state)
        self.missing_volume = None
        self.control_internal = True
        self.control_driver = "bridge"
        self.volume_names = (
            preflight.VAULT_PROJECT + "_vault_data",
            preflight.VAULT_PROJECT + "_backend_agent_auth",
            preflight.VAULT_PROJECT + "_backend_rendered",
            preflight.APP_PROJECT + "_postgres_data",
        )
        self.containers = {}
        for kind, project, services in (
            ("vault", preflight.VAULT_PROJECT, ("vault",)),
            ("app", preflight.APP_PROJECT, preflight.APP_SERVICES),
        ):
            files = (
                [self.root / "infra/vault/server/compose.yml",
                 self.state / "vault.override.yml"]
                if kind == "vault" else
                [self.root / "compose.yml", self.root / "compose.vault.yml",
                 self.state / "app.override.yml"]
            )
            for service in services:
                service_files = list(files)
                if kind == "app" and service in (
                    "nginx", "frontend", "collector", "docker-proxy", "graph-engine"
                ):
                    service_files.append(self.state / "full-stack.override.yml")
                name = project + "-" + service + "-1"
                self.containers[name] = {
                    "Config": {"Labels": {
                        "com.docker.compose.project": project,
                        "com.docker.compose.service": service,
                        "com.docker.compose.project.config_files":
                            ",".join(str(p) for p in service_files),
                        "com.docker.compose.project.working_dir":
                            str(self.root / "infra/vault/server") if kind == "vault"
                            else str(self.root),
                    }},
                    "Mounts": [],
                    "NetworkSettings": {"Networks": {
                        preflight.VAULT_PROJECT + "_vault_private": {}
                    }},
                }
        vault = self.containers[preflight.VAULT_PROJECT + "-vault-1"]
        agent = self.containers[preflight.APP_PROJECT + "-vault-backend-agent-1"]
        backend = self.containers[preflight.APP_PROJECT + "-backend-1"]
        postgres = self.containers[preflight.APP_PROJECT + "-postgres-1"]
        self.add_volume(vault, "/vault/data", self.volume_names[0])
        self.add_volume(agent, "/vault/auth", self.volume_names[1], rw=False)
        self.add_volume(agent, "/vault/rendered", self.volume_names[2])
        self.add_volume(backend, "/run/secrets/backend", self.volume_names[2], rw=False)
        self.add_volume(postgres, "/var/lib/postgresql", self.volume_names[3])
        self.add_bind(vault, "/vault/tls", self.state / "tls", rw=False)
        self.add_bind(agent, "/vault/tls/ca.crt", self.state / "tls/ca.crt", rw=False)
        default = preflight.APP_PROJECT + "_default"
        control = preflight.APP_PROJECT + "_collector_control"
        for service in preflight.APP_SERVICES:
            if service == "vault-backend-agent":
                continue
            attached = (
                {control} if service == "docker-proxy"
                else {default, control} if service == "collector"
                else {default}
            )
            container = self.containers[
                preflight.APP_PROJECT + "-" + service + "-1"
            ]
            container["NetworkSettings"]["Networks"] = {
                name: {} for name in attached
            }
        self.app_model = self.make_app_model()
        self.vault_model = self.make_vault_model()

    def add_volume(self, container, destination, name, rw=True):
        container["Mounts"].append({
            "Destination": destination, "Name": name, "Type": "volume", "RW": rw,
        })

    def add_bind(self, container, destination, path, rw=False):
        container["Mounts"].append({
            "Destination": destination, "Source": str(path), "Type": "bind", "RW": rw,
        })

    def ca_service(self):
        return {"volumes": [{
            "type": "bind", "source": str(self.state / "tls/ca.crt"),
            "target": "/vault/tls/ca.crt", "read_only": True,
        }]}

    def make_app_model(self):
        services = {name: {} for name in preflight.APP_SERVICES}
        services.update({
            name: self.ca_service() for name in preflight.APP_CA_SERVICES
        })
        services["docker-proxy"]["networks"] = {
            "collector_control": None
        }
        services["collector"]["networks"] = {
            "default": None,
            "collector_control": None,
        }
        services["backend"]["environment"] = {
            "DATABASE_URL_FILE": "/run/secrets/backend/database_url",
        }
        services["postgres"]["environment"] = {
            "POSTGRES_USER": "transcendence", "POSTGRES_DB": "transcendence",
        }
        return {
            "name": preflight.APP_PROJECT,
            "services": services,
            "volumes": {
                "backend_vault_auth": {"external": True, "name": self.volume_names[1]},
                "backend_vault_rendered": {"external": True, "name": self.volume_names[2]},
                "postgres_data": {"name": self.volume_names[3]},
                "migrator_vault_auth": {
                    "name": preflight.APP_PROJECT + "_migrator_vault_auth"},
                "migrator_vault_rendered": {
                    "name": preflight.APP_PROJECT + "_migrator_vault_rendered"},
                "postgres_provisioner_vault_auth": {
                    "name": preflight.APP_PROJECT + "_postgres_provisioner_vault_auth"},
                "postgres_provisioner_rendered": {
                    "name": preflight.APP_PROJECT + "_postgres_provisioner_rendered"},
            },
            "networks": {
                "vault_private": {
                    "external": True,
                    "name": preflight.VAULT_PROJECT + "_vault_private",
                },
                "default": {
                    "name": preflight.APP_PROJECT + "_default",
                },
                "collector_control": {
                    "name": preflight.APP_PROJECT + "_collector_control",
                    "driver": "bridge",
                    "internal": True,
                },
            },
        }

    def make_vault_model(self):
        services = {name: self.ca_service() for name in preflight.VAULT_CA_SERVICES}
        for name, readonly in (("vault", True),
                               ("vault-storage-permissions", False),
                               ("vault-tls-files", False)):
            services[name] = {"volumes": [{
                "type": "bind", "source": str(self.state / "tls"),
                "target": "/vault/tls", "read_only": readonly,
            }]}
        return {
            "name": preflight.VAULT_PROJECT,
            "services": services,
            "volumes": {
                "vault_data": {"name": self.volume_names[0]},
                "backend_agent_auth": {"name": self.volume_names[1]},
                "backend_rendered": {"name": self.volume_names[2]},
            },
            "networks": {"vault_private": {
                "name": preflight.VAULT_PROJECT + "_vault_private",
                "internal": True,
            }},
        }

    def fake_json(self, command, env=None):
        if command[:2] == ["docker", "inspect"]:
            return [deepcopy(self.containers[command[2]])]
        if command[:3] == ["docker", "volume", "inspect"]:
            if command[3] == self.missing_volume:
                return []
            owner = (preflight.VAULT_PROJECT
                     if command[3].startswith(preflight.VAULT_PROJECT + "_")
                     else preflight.APP_PROJECT)
            return [{"Name": command[3], "Labels": {
                "com.docker.compose.project": owner,
            }}]
        if command[:3] == ["docker", "network", "inspect"]:
            if command[3] == preflight.APP_PROJECT + "_collector_control":
                return [{
                    "Name": command[3],
                    "Internal": self.control_internal,
                    "Driver": self.control_driver,
                    "Labels": {
                        "com.docker.compose.project": preflight.APP_PROJECT,
                    },
                }]
            return [{"Name": command[3], "Internal": True, "Labels": {
                "com.docker.compose.project": preflight.VAULT_PROJECT,
            }}]
        if command[:2] == ["docker", "compose"]:
            self.assertNotIn("POSTGRES_PASSWORD", env)
            self.assertEqual(env["FT_DEV_TLS_DIR"], str(self.state / "tls"))
            self.assertIn("--env-file", command)
            self.assertIn("--profile", command)
            self.assertEqual(command[command.index("--profile") + 1], "*")
            self.assertEqual(command[-3:], ["config", "--format", "json"])
            return deepcopy(self.app_model if preflight.APP_PROJECT in command
                            else self.vault_model)
        raise AssertionError("Unexpected command")

    def run_preflight(self):
        with patch.object(preflight, "json_command", side_effect=self.fake_json), \
             patch.object(preflight, "execute", return_value=""):
            self.pre.run()

    def test_compose_model_includes_profile_gated_resources(self):
        with patch.object(preflight, "json_command", side_effect=self.fake_json):
            app = self.pre.compose_model(
                (self.root / "compose.yml",), preflight.APP_PROJECT
            )
            vault = self.pre.compose_model(
                (self.root / "infra/vault/server/compose.yml",),
                preflight.VAULT_PROJECT,
            )
        self.assertIn("migrator_vault_auth", app["volumes"])
        self.assertIn("postgres_provisioner_rendered", app["volumes"])
        self.assertIn("backend_agent_auth", vault["volumes"])

    def test_existing_installation_passes(self):
        self.run_preflight()

    def test_missing_override_fails_closed(self):
        (self.state / "app.override.yml").unlink()
        with self.assertRaisesRegex(preflight.PreflightError, "override"):
            self.run_preflight()

    def test_symlinked_tls_file_rejected(self):
        ca = self.state / "tls/ca.crt"
        ca.unlink()
        ca.symlink_to(self.state / "tls/vault.crt")
        with self.assertRaisesRegex(preflight.PreflightError, "TLS"):
            self.run_preflight()

    def test_wrong_project_owner_fails(self):
        agent = self.containers[preflight.APP_PROJECT + "-vault-backend-agent-1"]
        agent["Config"]["Labels"]["com.docker.compose.project"] = "unrelated"
        with self.assertRaisesRegex(preflight.PreflightError, "owner"):
            self.run_preflight()

    def test_changed_override_provenance_fails(self):
        backend = self.containers[preflight.APP_PROJECT + "-backend-1"]
        backend["Config"]["Labels"]["com.docker.compose.project.config_files"] = "other"
        with self.assertRaisesRegex(preflight.PreflightError, "provenance"):
            self.run_preflight()

    def test_new_raft_volume_fails(self):
        vault = self.containers[preflight.VAULT_PROJECT + "-vault-1"]
        vault["Mounts"][0]["Name"] = "other_vault_data"
        with self.assertRaisesRegex(preflight.PreflightError, "Raft"):
            self.run_preflight()

    def test_wrong_postgres_volume_fails(self):
        pg = self.containers[preflight.APP_PROJECT + "-postgres-1"]
        pg["Mounts"][0]["Name"] = "new_postgres_data"
        with self.assertRaisesRegex(preflight.PreflightError, "PostgreSQL"):
            self.run_preflight()

    def test_new_single_file_provenance_passes(self):
        for name, container in self.containers.items():
            project = (preflight.VAULT_PROJECT
                       if name.startswith(preflight.VAULT_PROJECT + "-")
                       else preflight.APP_PROJECT)
            compose = (self.root / "infra/vault/server/compose.yml"
                       if project == preflight.VAULT_PROJECT
                       else self.root / "compose.yml")
            container["Config"]["Labels"]["com.docker.compose.project.config_files"] = str(compose)
        self.run_preflight()
        self.assertFalse(self.pre.legacy)

    def test_unexpected_full_stack_override_rejected(self):
        frontend = self.containers[preflight.APP_PROJECT + "-frontend-1"]
        key = "com.docker.compose.project.config_files"
        frontend["Config"]["Labels"][key] += ",/tmp/untrusted.yml"
        with self.assertRaisesRegex(preflight.PreflightError, "provenance"):
            self.run_preflight()

    def test_symlinked_legacy_full_stack_rejected(self):
        file = self.state / "full-stack.override.yml"
        file.unlink()
        file.symlink_to(self.state / "app.override.yml")
        with self.assertRaisesRegex(preflight.PreflightError, "override"):
            self.run_preflight()

    def test_writable_vault_tls_mount_without_read_only_passes(self):
        # Docker Compose may omit read_only for writable bind mounts.
        for name in ("vault-storage-permissions", "vault-tls-files"):
            self.vault_model["services"][name]["volumes"][0].pop("read_only")
        self.run_preflight()

    def test_missing_read_only_on_vault_server_rejected(self):
        self.vault_model["services"]["vault"]["volumes"][0].pop("read_only")
        with self.assertRaisesRegex(preflight.PreflightError, "Vault TLS mount"):
            self.run_preflight()

    def test_missing_read_only_on_agent_ca_rejected(self):
        self.app_model["services"]["vault-backend-agent"]["volumes"][0].pop("read_only")
        with self.assertRaisesRegex(preflight.PreflightError, "CA mount"):
            self.run_preflight()

    def test_wrong_tls_mount_fails(self):
        vault = self.containers[preflight.VAULT_PROJECT + "-vault-1"]
        vault["Mounts"][1]["Source"] = "/repo/wrong/tls"
        with self.assertRaisesRegex(preflight.PreflightError, "TLS mount"):
            self.run_preflight()

    def test_cleanroom_profile_rejected(self):
        self.app_model["services"]["frontend"]["profiles"] = ["cleanroom-excluded"]
        with self.assertRaisesRegex(preflight.PreflightError, "profile-excluded"):
            self.run_preflight()

    def test_postgres_bootstrap_password_rejected(self):
        self.app_model["services"]["postgres"]["environment"][
            "POSTGRES_PASSWORD"] = "password-not-to-be-printed"
        with self.assertRaisesRegex(preflight.PreflightError, "bootstrap password"):
            self.run_preflight()

    def test_backend_env_secret_rejected(self):
        self.app_model["services"]["backend"]["environment"][
            "DATABASE_URL"] = "never-print-this"
        with self.assertRaisesRegex(preflight.PreflightError, "file-only"):
            self.run_preflight()

    def test_ca_of_migration_agent_rejected(self):
        self.app_model["services"]["vault-migrator-agent"]["volumes"][0][
            "source"] = "/wrong/ca.crt"
        with self.assertRaisesRegex(preflight.PreflightError, "CA mount"):
            self.run_preflight()

    def test_wrong_private_network_rejected(self):
        self.vault_model["networks"]["vault_private"]["internal"] = False
        with self.assertRaisesRegex(preflight.PreflightError, "Vault network"):
            self.run_preflight()

    def test_missing_migrator_volume_rejected(self):
        self.missing_volume = preflight.APP_PROJECT + "_migrator_vault_auth"
        with self.assertRaisesRegex(preflight.PreflightError, "volume missing"):
            self.run_preflight()

    def test_unexpected_application_volume_rejected(self):
        self.app_model["volumes"]["extra"] = {"name": "new_data"}
        with self.assertRaisesRegex(preflight.PreflightError, "volume inventory"):
            self.run_preflight()

    def test_changed_migrator_volume_rejected(self):
        self.app_model["volumes"]["migrator_vault_auth"]["name"] = "other"
        with self.assertRaisesRegex(preflight.PreflightError, "application volume"):
            self.run_preflight()

    def test_changed_vault_auth_volume_rejected(self):
        self.vault_model["volumes"]["backend_agent_auth"]["name"] = "other"
        with self.assertRaisesRegex(preflight.PreflightError, "Vault volume"):
            self.run_preflight()

    def test_collector_network_must_be_internal(self):
        self.control_internal = False
        with self.assertRaisesRegex(
            preflight.PreflightError, "Collector control network"
        ):
            self.run_preflight()

    def test_docker_proxy_cannot_join_default_network(self):
        self.app_model["services"]["docker-proxy"]["networks"][
            "default"
        ] = None
        with self.assertRaisesRegex(
            preflight.PreflightError, "Docker Proxy isolation"
        ):
            self.run_preflight()

    def test_collector_requires_both_networks(self):
        self.app_model["services"]["collector"]["networks"].pop(
            "collector_control"
        )
        with self.assertRaisesRegex(
            preflight.PreflightError, "Collector network attachment"
        ):
            self.run_preflight()

    def test_existing_docker_proxy_default_network_rejected(self):
        proxy = self.containers[
            preflight.APP_PROJECT + "-docker-proxy-1"
        ]
        proxy["NetworkSettings"]["Networks"][
            preflight.APP_PROJECT + "_default"
        ] = {}
        with self.assertRaisesRegex(
            preflight.PreflightError,
            "Existing Collector network attachment"
        ):
            self.run_preflight()

    def test_published_postgres_port_rejected(self):
        self.app_model["services"]["postgres"]["ports"] = [{
            "target": 5432, "published": "5432"}]
        with self.assertRaisesRegex(preflight.PreflightError, "published runtime port"):
            self.run_preflight()


if __name__ == "__main__":
    unittest.main()
