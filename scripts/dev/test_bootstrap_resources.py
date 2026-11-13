"""Offline safety tests for clean-machine Docker resource initialization."""
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "bootstrap_resources", Path(__file__).with_name("bootstrap_resources.py")
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.state = {}
        self.creates = []

    def fake_inspect(self, kind, name):
        return self.state.get((kind, name))

    def fake_command(self, args, **kwargs):
        self.creates.append(args)
        kind = args[1]
        if kind == "network":
            name = args[-1]
            project, internal, private = mod.NETWORKS[name]
            self.state[(kind, name)] = {
                "Name": name, "Driver": "bridge", "Internal": "--internal" in args,
                "Labels": {
                    "com.docker.compose.project": project,
                    "com.docker.compose.network": internal,
                },
            }
            self.assertEqual(private, self.state[(kind, name)]["Internal"])
        elif kind == "volume":
            name = args[-1]
            project, internal = mod.VOLUMES[name]
            self.state[(kind, name)] = {
                "Name": name, "Driver": "local",
                "Labels": {
                    "com.docker.compose.project": project,
                    "com.docker.compose.volume": internal,
                },
            }
        else:
            self.fail(f"Unexpected Docker mutation: {args}")

    def test_apply_creates_only_expected_resources(self):
        with patch.object(mod, "inspect", side_effect=self.fake_inspect), patch.object(mod, "command", side_effect=self.fake_command):
            existing = mod.scan()
            self.assertFalse(any(existing.values()))
            mod.create_missing(existing)
            self.assertEqual(len(self.creates), 11)
            verified = mod.scan()
            self.assertTrue(all(verified.values()))
            mod.create_missing(verified)
            self.assertEqual(len(self.creates), 11)

    def test_existing_unsafe_network_refused_without_creating(self):
        self.state[("network", "transcendence-vault-dev_vault_private")] = {
            "Name": "transcendence-vault-dev_vault_private", "Driver": "bridge",
            "Internal": False,
            "Labels": {"com.docker.compose.project": mod.VAULT_PROJECT,
                       "com.docker.compose.network": "vault_private"},
        }
        with patch.object(mod, "inspect", side_effect=self.fake_inspect), patch.object(mod, "command", side_effect=self.fake_command):
            with self.assertRaises(mod.ResourceError):
                mod.scan()
            self.assertEqual(self.creates, [])

    def test_unowned_volume_refused(self):
        name = "transcendence-app-dev_postgres_data"
        self.state[("volume", name)] = {"Name": name, "Driver": "local", "Labels": {}}
        with patch.object(mod, "inspect", side_effect=self.fake_inspect), patch.object(mod, "command", side_effect=self.fake_command):
            with self.assertRaises(mod.ResourceError):
                mod.scan()
            self.assertEqual(self.creates, [])

    def test_networks_have_exact_isolation(self):
        self.assertEqual(len(mod.NETWORKS), 3)
        self.assertEqual(len(mod.VOLUMES), 8)
        self.assertTrue(mod.NETWORKS["transcendence-vault-dev_vault_private"][2])
        self.assertTrue(mod.NETWORKS["transcendence-app-dev_collector_control"][2])
        self.assertFalse(mod.NETWORKS["transcendence-app-dev_default"][2])

    def test_compose_contract_accepts_expected_layout(self):
        vault = {
            "volumes": {
                key: {"name": name}
                for name, (project, key) in mod.VOLUMES.items()
                if project == mod.VAULT_PROJECT
            },
            "networks": {"vault_private": {
                "name": "transcendence-vault-dev_vault_private", "external": True
            }},
        }
        app = {
            "volumes": {
                key: {"name": name}
                for name, (project, key) in mod.VOLUMES.items()
                if project == mod.APP_PROJECT
            },
            "networks": {
                key: {"name": name, "external": True}
                for name, (project, key, private) in mod.NETWORKS.items()
                if project == mod.APP_PROJECT
            },
        }
        app["volumes"].update({
            "backend_vault_auth": {
                "name": "transcendence-vault-dev_backend_agent_auth", "external": True
            },
            "backend_vault_rendered": {
                "name": "transcendence-vault-dev_backend_rendered", "external": True
            },
        })
        app["networks"]["vault_private"] = {
            "name": "transcendence-vault-dev_vault_private", "external": True
        }
        with patch.object(mod, "compose_model", side_effect=lambda project, _: vault if project == mod.VAULT_PROJECT else app):
            mod.validate_compose_contract()

    def test_compose_contract_rejects_unknown_volume(self):
        def fake_model(project, path):
            if project == mod.VAULT_PROJECT:
                return {"volumes": {"vault_data": {"name": "v"}}}
            return {"volumes": {}, "networks": {}}
        with patch.object(mod, "compose_model", side_effect=fake_model):
            with self.assertRaises(mod.ResourceError):
                mod.validate_compose_contract()


if __name__ == "__main__":
    unittest.main()
