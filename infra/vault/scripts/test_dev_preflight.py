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
        for name in ("app.override.yml", "vault.override.yml"):
            (self.state / name).write_text("services: {}\n")
        for name in ("ca.crt", "vault.crt", "vault.key"):
            (self.state / "tls" / name).write_text("fake certificate fixture")
        self.pre = preflight.Preflight(self.root, self.state)
        self.missing_volume = None
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
                name = project + "-" + service + "-1"
                self.containers[name] = {
                    "Config": {"Labels": {
                        "com.docker.compose.project": project,
                        "com.docker.compose.service": service,
                        "com.docker.compose.project.config_files":
                            ",".join(str(p) for p in files),
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
        services.update({name: self.ca_service() for name in preflight.APP_CA_SERVICES})
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
            "networks": {"vault_private": {
                "external": True,
                "name": preflight.VAULT_PROJECT + "_vault_private",
            }},
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
            return [{"Name": command[3], "Internal": True, "Labels": {
                "com.docker.compose.project": preflight.VAULT_PROJECT,
            }}]
        if command[:2] == ["docker", "compose"]:
            self.assertEqual(env["POSTGRES_PASSWORD"], "")
            self.assertEqual(env["FT_DEV_VAULT_PROJECT"], preflight.VAULT_PROJECT)
            self.assertIn("--env-file", command)
            return deepcopy(self.app_model if preflight.APP_PROJECT in command
                            else self.vault_model)
        raise AssertionError("Unexpected command")

    def run_preflight(self):
        with patch.object(preflight, "json_command", side_effect=self.fake_json), \
             patch.object(preflight, "execute", return_value=""):
            self.pre.run()

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

    def test_published_postgres_port_rejected(self):
        self.app_model["services"]["postgres"]["ports"] = [{
            "target": 5432, "published": "5432"}]
        with self.assertRaisesRegex(preflight.PreflightError, "published runtime port"):
            self.run_preflight()


if __name__ == "__main__":
    unittest.main()
