#!/usr/bin/env python3
"""Validate the two-file Vault-only Compose architecture without starting Docker."""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TLS = Path("/tmp/transcendence-compose-validation-tls")
APP = ROOT / "compose.yml"
VAULT = ROOT / "infra/vault/server/compose.yml"

APP_SERVICES = {
    "backend", "frontend", "postgres", "nginx", "collector",
    "docker-proxy", "graph-engine", "vault-backend-agent",
}
CA_AGENTS = {
    "vault-backend-agent", "vault-migrator-agent",
    "vault-migrator-provision", "vault-migrator-rotate",
    "vault-postgres-provisioner-agent",
    "vault-postgres-provisioner-provision",
    "vault-postgres-provisioner-rotate",
}


def compose_model(path, public=None):
    if not shutil.which("docker"):
        raise unittest.SkipTest("Docker Compose CLI is not installed")
    env = {
        key: value for key, value in os.environ.items()
        if key in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")
    }
    env.update({
        "FT_DEV_TLS_DIR": str(TLS),
        "POSTGRES_USER": "transcendence",
        "POSTGRES_DB": "transcendence",
    })
    if public is None:
        command = [sys.executable, str(ROOT / "scripts/dev/configuration.py"), "compose"]
    else:
        env.update(public)
        command = ["docker", "compose", "--env-file", "/dev/null"]
    result = subprocess.run(
        [*command, "-f", str(path), "--profile", "*", "config", "--format", "json"],
        capture_output=True, text=True, check=False, env=env, timeout=30,
    )
    if result.returncode:
        raise AssertionError("Docker Compose configuration failed")
    return json.loads(result.stdout)


def mount(service, destination):
    mounts = [entry for entry in service.get("volumes", [])
              if entry.get("target") == destination]
    if len(mounts) != 1:
        raise AssertionError("Expected exactly one mount: " + destination)
    return mounts[0]


class UnifiedComposeTests(unittest.TestCase):
    def test_only_two_normal_compose_sources(self):
        self.assertTrue(APP.is_file())
        self.assertTrue(VAULT.is_file())
        for path in (
            ROOT / "compose.vault.yml",
            ROOT / "compose.dev.yml",
            ROOT / "infra/vault/runtime/app.override.yml",
            ROOT / "infra/vault/runtime/vault.override.yml",
        ):
            self.assertFalse(path.exists(), str(path))

    def test_custom_public_endpoints(self):
        public = {
            "DEV_DOMAIN": "example.test", "DEV_HTTP_PORT": "9080",
            "DEV_HTTPS_PORT": "9443", "DEV_VAULT_UI_PORT": "9200",
            "APP_ORIGIN": "https://example.test:9443",
            "GRAFANA_ROOT_URL": "https://example.test:9443/grafana/",
            "SESSION_TTL_HOURS": "24", "COOKIE_SECURE": "true",
        }
        services = compose_model(APP, public)["services"]
        self.assertEqual(services["backend"]["environment"]["APP_ORIGIN"], public["APP_ORIGIN"])
        self.assertEqual(str(services["backend"]["environment"]["SESSION_TTL_HOURS"]), "24")
        self.assertEqual(services["backend"]["environment"]["COOKIE_SECURE"], "true")
        self.assertEqual(services["frontend"]["environment"]["DEV_DOMAIN"], "example.test")
        self.assertEqual(services["grafana"]["environment"]["GF_SERVER_ROOT_URL"], public["GRAFANA_ROOT_URL"])
        nginx = services["nginx"]
        self.assertEqual(nginx["environment"]["DEV_DOMAIN"], "example.test")
        self.assertEqual(str(nginx["environment"]["DEV_HTTPS_PORT"]), "9443")
        self.assertEqual({(str(p["target"]), str(p["published"]), p["host_ip"]) for p in nginx["ports"]},
                         {("8080", "9080", "127.0.0.1"), ("8443", "9443", "127.0.0.1")})
        gateway = compose_model(ROOT / "infra/vault/ui/compose.yml", public)["services"]["gateway"]
        self.assertEqual({(str(p["target"]), str(p["published"]), p["host_ip"]) for p in gateway["ports"]},
                         {("8200", "9200", "127.0.0.1")})

    def test_application_invariants(self):
        model = compose_model(APP)
        self.assertEqual(model["name"], "transcendence-app-dev")
        services = model["services"]
        self.assertTrue(APP_SERVICES.issubset(services))
        for name in APP_SERVICES:
            self.assertFalse(services[name].get("profiles"), name)
        for name in (
            "backend", "frontend", "postgres", "collector",
            "docker-proxy", "graph-engine",
        ):
            self.assertFalse(services[name].get("ports"), name)
        backend = services["backend"]
        self.assertEqual(backend["environment"]["DATABASE_URL_FILE"],
                         "/run/secrets/backend/database_url")
        self.assertNotIn("DATABASE_URL", backend["environment"])
        self.assertTrue(backend["read_only"])
        self.assertIn("ALL", backend["cap_drop"])
        self.assertTrue(
            any(m.get("source") == "backend_vault_rendered"
                for m in backend.get("volumes", []))
        )
        postgres = services["postgres"]
        self.assertNotIn("POSTGRES_PASSWORD", postgres["environment"])
        self.assertEqual(
            mount(postgres, "/var/lib/postgresql")["source"],
            "postgres_data",
        )
        self.assertEqual(
            mount(services["vault-backend-agent"], "/vault/auth")["source"],
            "backend_vault_auth",
        )
        for name in CA_AGENTS:
            m = mount(services[name], "/vault/tls/ca.crt")
            self.assertEqual(m["source"], str(TLS / "ca.crt"), name)
            self.assertTrue(m["read_only"], name)
        control = model["networks"]["collector_control"]
        self.assertEqual(
            control["name"],
            "transcendence-app-dev_collector_control"
        )
        self.assertTrue(control["external"])
        self.assertEqual(
            model["networks"]["default"]["name"],
            "transcendence-app-dev_default"
        )
        self.assertTrue(model["networks"]["default"]["external"])
        self.assertEqual(
            set(services["docker-proxy"]["networks"]),
            {"collector_control"}
        )
        self.assertEqual(
            set(services["collector"]["networks"]),
            {"default", "collector_control"}
        )
        self.assertNotIn(
            "collector_control",
            services["backend"].get("networks", {})
        )
        network = model["networks"]["vault_private"]
        self.assertTrue(network["external"])
        self.assertEqual(network["name"],
                         "transcendence-vault-dev_vault_private")
        self.assertEqual(
            model["volumes"]["backend_vault_rendered"]["name"],
            "transcendence-vault-dev_backend_rendered",
        )
        self.assertEqual(
            model["volumes"]["backend_vault_auth"]["name"],
            "transcendence-vault-dev_backend_agent_auth",
        )
        self.assertEqual(
            model["volumes"]["postgres_data"]["name"],
            "transcendence-app-dev_postgres_data",
        )

    def test_vault_server_invariants(self):
        model = compose_model(VAULT)
        self.assertEqual(model["name"], "transcendence-vault-dev")
        services = model["services"]
        vault = services["vault"]
        self.assertFalse(vault.get("ports"))
        m = mount(vault, "/vault/tls")
        self.assertEqual(m["source"], str(TLS))
        self.assertTrue(m["read_only"])
        self.assertEqual(
            mount(vault, "/vault/data")["source"],
            "vault_data",
        )
        for name in ("vault-backend-provision", "vault-backend-rotate",
                     "vault-bootstrap"):
            m = mount(services[name], "/vault/tls/ca.crt")
            self.assertEqual(m["source"], str(TLS / "ca.crt"), name)
            self.assertTrue(m["read_only"])
        net = model["networks"]["vault_private"]
        self.assertTrue(net["external"])
        self.assertEqual(net["name"],
                         "transcendence-vault-dev_vault_private")


if __name__ == "__main__":
    unittest.main()
