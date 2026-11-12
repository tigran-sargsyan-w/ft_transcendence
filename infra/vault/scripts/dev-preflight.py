#!/usr/bin/env python3
"""Read-only checks for the existing Vault-backed development installation (#115)."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP_PROJECT = "transcendence-app-dev"
VAULT_PROJECT = "transcendence-vault-dev"
APP_SERVICES = (
    "nginx", "frontend", "collector", "docker-proxy", "graph-engine",
    "backend", "vault-backend-agent", "postgres",
)
APP_CA_SERVICES = (
    "vault-backend-agent", "vault-migrator-agent", "vault-migrator-provision",
    "vault-migrator-rotate", "vault-postgres-provisioner-agent",
    "vault-postgres-provisioner-provision", "vault-postgres-provisioner-rotate",
)
VAULT_CA_SERVICES = (
    "vault-backend-provision", "vault-backend-rotate", "vault-bootstrap",
)


class PreflightError(Exception):
    """An error message containing no secret-bearing subprocess output."""


def execute(command, env=None):
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False,
            timeout=20, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PreflightError("Required Docker/OpenSSL command unavailable or timed out") from None
    if result.returncode:
        raise PreflightError("Check command failed (" + command[0] + ")")
    return result.stdout


def json_command(command, env=None):
    raw = execute(command, env=env)
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        raise PreflightError("Malformed Docker/Compose response") from None


def require(condition, detail):
    if not condition:
        raise PreflightError(detail)


def mount(container, destination, source, kind, *, readonly=None):
    matches = [m for m in container.get("Mounts", [])
               if m.get("Destination") == destination]
    if len(matches) != 1:
        return False
    current = matches[0]
    if current.get("Type") != kind:
        return False
    value = current.get("Name") if kind == "volume" else current.get("Source")
    if value != str(source):
        return False
    if readonly is not None and current.get("RW") != (not readonly):
        return False
    return True


def compose_mount(service, destination, source, *, readonly=True):
    volumes = service.get("volumes", [])
    matches = [m for m in volumes if m.get("target") == destination]
    return (len(matches) == 1 and matches[0].get("type") == "bind"
            and matches[0].get("source") == str(source)
            and matches[0].get("read_only", False) is readonly)


class Preflight:
    def __init__(self, root=ROOT, state=None):
        self.root = Path(root).resolve()
        self.state = Path(state) if state is not None else (
            Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
            / "transcendence/dev"
        )
        self.tls = self.state / "tls"
        self.network = VAULT_PROJECT + "_vault_private"
        self.containers = {}
        self.legacy = False

    def ok(self, description):
        print("PASS: " + description)

    def paths(self):
        for path in (self.state, self.tls):
            require(path.is_dir() and not path.is_symlink(),
                    "Local runtime state directory missing or unsafe")
        require(self.state.is_absolute() and self.state.resolve() != self.root
                and self.root not in self.state.resolve().parents,
                "Runtime state must be outside the repository")
        for name in ("ca.crt", "vault.crt", "vault.key"):
            path = self.tls / name
            require(path.is_file() and not path.is_symlink() and path.stat().st_size > 0,
                    "Expected local TLS file missing or unsafe: " + name)
        self.ok("Existing local runtime profile and TLS files")

    def certificates(self):
        ca, cert = self.tls / "ca.crt", self.tls / "vault.crt"
        execute(["openssl", "verify", "-purpose", "sslserver", "-verify_hostname",
                 "vault", "-CAfile", str(ca), str(cert)])
        execute(["openssl", "x509", "-in", str(cert), "-noout", "-checkend", "0"])
        self.ok("Vault TLS chain, hostname and expiry")

    def inspect(self, name):
        data = json_command(["docker", "inspect", name])
        require(isinstance(data, list) and len(data) == 1,
                "Ambiguous or missing Docker container: " + name)
        return data[0]

    def docker_identity(self):
        # Only exact known container origins are allowed during the cutover.
        # Historical labels do not authorize recreating those containers.
        vault_file = self.root / "infra/vault/server/compose.yml"
        app_file = self.root / "compose.yml"
        old_app = [app_file, self.root / "compose.vault.yml",
                   self.state / "app.override.yml"]
        full_stack = {"nginx", "frontend", "collector",
                      "docker-proxy", "graph-engine"}
        for kind, project, services in (
            ("vault", VAULT_PROJECT, ("vault",)),
            ("app", APP_PROJECT, APP_SERVICES),
        ):
            for service in services:
                name = project + "-" + service + "-1"
                container = self.inspect(name)
                labels = container.get("Config", {}).get("Labels") or {}
                require(labels.get("com.docker.compose.project") == project
                        and labels.get("com.docker.compose.service") == service,
                        "Unexpected Docker Compose owner: " + name)
                actual = labels.get("com.docker.compose.project.config_files", "").split(",")
                target = [str(vault_file if kind == "vault" else app_file)]
                if kind == "vault":
                    legacy = [vault_file, self.state / "vault.override.yml"]
                else:
                    legacy = list(old_app)
                    if service in full_stack:
                        legacy.append(self.state / "full-stack.override.yml")
                if actual == [str(path) for path in legacy]:
                    for path in legacy:
                        if self.state in path.parents:
                            require(path.is_file() and not path.is_symlink(),
                                    "Historical override missing or unsafe")
                    self.legacy = True
                else:
                    require(actual == target,
                            "Existing Compose file provenance differs: " + name)
                working_dir = (self.root / "infra/vault/server"
                               if kind == "vault" else self.root)
                require(labels.get("com.docker.compose.project.working_dir")
                        == str(working_dir),
                        "Unexpected Compose working directory: " + name)
                self.containers[service if kind == "app" else "vault"] = container
        self.ok("Existing container identities and Compose provenance")

    def volumes_and_network(self):
        names = (
            VAULT_PROJECT + "_vault_data",
            VAULT_PROJECT + "_backend_agent_auth",
            VAULT_PROJECT + "_backend_rendered",
            APP_PROJECT + "_postgres_data",
        )
        for name in names:
            volume = json_command(["docker", "volume", "inspect", name])
            require(isinstance(volume, list) and len(volume) == 1
                    and volume[0].get("Name") == name,
                    "Expected existing volume not found: " + name)

        require(mount(self.containers["vault"], "/vault/data", names[0], "volume"),
                "Vault Raft volume differs")
        agent = self.containers["vault-backend-agent"]
        require(mount(agent, "/vault/auth", names[1], "volume", readonly=True),
                "Vault Agent AppRole volume differs")
        require(mount(agent, "/vault/rendered", names[2], "volume"),
                "Vault Agent rendered-secret volume differs")
        require(mount(self.containers["backend"], "/run/secrets/backend", names[2],
                      "volume", readonly=True), "Backend secret mount differs")
        require(mount(self.containers["postgres"], "/var/lib/postgresql",
                      names[3], "volume"), "PostgreSQL data volume differs")
        self.ok("Existing Raft, Agent and PostgreSQL volumes")

        network = json_command(["docker", "network", "inspect", self.network])
        require(isinstance(network, list) and len(network) == 1
                and network[0].get("Name") == self.network
                and network[0].get("Internal") is True
                and (network[0].get("Labels") or {}).get("com.docker.compose.project")
                == VAULT_PROJECT, "Unexpected Vault private network")
        for service in ("vault", "vault-backend-agent"):
            attached = (self.containers[service].get("NetworkSettings") or {}).get("Networks") or {}
            require(self.network in attached,
                    "Unexpected Vault network attachment: " + service)
        self.ok("Existing private Vault network")

        control_name = APP_PROJECT + "_collector_control"
        default_name = APP_PROJECT + "_default"
        default_network = json_command(
            ["docker", "network", "inspect", default_name]
        )
        require(
            isinstance(default_network, list)
            and len(default_network) == 1
            and default_network[0].get("Name") == default_name
            and default_network[0].get("Driver") == "bridge"
            and default_network[0].get("Internal") is False
            and (default_network[0].get("Labels") or {}).get(
                "com.docker.compose.project"
            ) == APP_PROJECT,
            "Unexpected existing application default network"
        )
        control = json_command(
            ["docker", "network", "inspect", control_name]
        )
        require(
            isinstance(control, list) and len(control) == 1
            and control[0].get("Name") == control_name
            and control[0].get("Internal") is True
            and control[0].get("Driver") == "bridge"
            and (control[0].get("Labels") or {}).get(
                "com.docker.compose.project"
            ) == APP_PROJECT,
            "Unexpected existing Collector control network"
        )

        expected = {
            "docker-proxy": {control_name},
            "collector": {default_name, control_name},
        }
        for service in APP_SERVICES:
            networks = set(
                ((self.containers[service].get("NetworkSettings")
                  or {}).get("Networks") or {})
            )
            if service in expected:
                require(
                    networks == expected[service],
                    "Existing Collector network attachment differs: "
                    + service
                )
            else:
                require(
                    control_name not in networks,
                    "Unexpected Collector control network access: "
                    + service
                )
        self.ok("Existing Collector network isolation")

    def tls_mounts(self):
        require(mount(self.containers["vault"], "/vault/tls", self.tls, "bind",
                      readonly=True), "Vault server TLS mount differs")
        require(mount(self.containers["vault-backend-agent"], "/vault/tls/ca.crt",
                      self.tls / "ca.crt", "bind", readonly=True),
                "Vault Agent CA mount differs")
        self.ok("Existing Vault and Agent TLS mounts")

    def compose_model(self, files, project):
        allowed = ("PATH", "HOME", "USER", "LANG", "DOCKER_HOST",
                   "DOCKER_CONTEXT", "DOCKER_CONFIG", "XDG_RUNTIME_DIR")
        env = {k: v for k, v in os.environ.items() if k in allowed}
        env.update({"FT_DEV_TLS_DIR": str(self.tls),
                    "POSTGRES_USER": "transcendence", "POSTGRES_DB": "transcendence"})
        command = ["docker", "compose", "--env-file", "/dev/null", "-p", project]
        for item in files:
            command += ["-f", str(item)]
        # Inspect maintenance/setup profiles too: they declare named volumes
        # required by the full target model, but are not started by --check.
        command += ["--profile", "*", "config", "--format", "json"]
        data = json_command(command, env=env)
        require(data.get("name") == project, "Unexpected generated Compose project")
        return data

    def target_models(self):
        app = self.compose_model((self.root / "compose.yml",), APP_PROJECT)
        vault = self.compose_model(
            (self.root / "infra/vault/server/compose.yml",), VAULT_PROJECT
        )
        services = app.get("services", {})
        for name in APP_SERVICES:
            require(name in services and not services[name].get("profiles"),
                    "Missing or profile-excluded runtime service: " + name)
        for name in ("backend", "frontend", "collector", "graph-engine",
                     "docker-proxy", "postgres"):
            require(not services[name].get("ports"),
                    "Unexpected published runtime port: " + name)
        backend_env = services["backend"].get("environment") or {}
        postgres_env = services["postgres"].get("environment") or {}
        require("DATABASE_URL" not in backend_env and
                backend_env.get("DATABASE_URL_FILE") == "/run/secrets/backend/database_url",
                "Backend did not retain file-only Vault secret delivery")
        require("POSTGRES_PASSWORD" not in postgres_env,
                "Runtime PostgreSQL bootstrap password must be absent")
        require(postgres_env.get("POSTGRES_USER") == "transcendence"
                and postgres_env.get("POSTGRES_DB") == "transcendence",
                "Unexpected PostgreSQL bootstrap identity")
        volumes = app.get("volumes", {})
        for key, expected in (
            ("backend_vault_auth", VAULT_PROJECT + "_backend_agent_auth"),
            ("backend_vault_rendered", VAULT_PROJECT + "_backend_rendered"),
        ):
            require(volumes.get(key, {}).get("name") == expected
                    and volumes[key].get("external") is True,
                    "Unexpected shared Vault volume: " + key)
        owned = (
            "postgres_data", "migrator_vault_auth", "migrator_vault_rendered",
            "postgres_provisioner_vault_auth", "postgres_provisioner_rendered",
        )
        require(set(volumes) == set(owned) | {
            "backend_vault_auth", "backend_vault_rendered",
        }, "Unexpected target application volume inventory")
        for key in owned:
            expected = APP_PROJECT + "_" + key
            require(volumes.get(key, {}).get("name") == expected
                    and not volumes[key].get("external", False),
                    "Unexpected target application volume: " + key)
        app_network = app.get("networks", {}).get("vault_private", {})
        require(app_network.get("name") == self.network
                and app_network.get("external") is True,
                "Application target Vault network differs")
        networks = app.get("networks", {})
        for key in ("collector_control", "default"):
            target = networks.get(key, {})
            require(
                target.get("name") == APP_PROJECT + "_" + key
                and target.get("external") is True,
                "Target application network differs: " + key
            )
        require(
            set(services["docker-proxy"].get("networks") or {})
            == {"collector_control"},
            "Target Docker Proxy isolation differs"
        )
        require(
            set(services["collector"].get("networks") or {})
            == {"default", "collector_control"},
            "Target Collector network attachment differs"
        )
        for name, service in services.items():
            if name not in ("collector", "docker-proxy"):
                require(
                    "collector_control"
                    not in (service.get("networks") or {}),
                    "Unexpected target Collector network access: " + name
                )
        self.ok("Target Collector network isolation")

        for name in APP_CA_SERVICES:
            require(name in services and compose_mount(services[name],
                    "/vault/tls/ca.crt", self.tls / "ca.crt"),
                    "Target Agent/helper CA mount differs: " + name)
        self.ok("Full application runtime model, isolation and file secrets")

        vault_services = vault.get("services", {})
        vault_volumes = vault.get("volumes", {})
        require(set(vault_volumes) == {
            "vault_data", "backend_agent_auth", "backend_rendered",
        }, "Unexpected target Vault volume inventory")
        for key in vault_volumes:
            require(vault_volumes[key].get("name") == VAULT_PROJECT + "_" + key
                    and not vault_volumes[key].get("external", False),
                    "Unexpected target Vault volume: " + key)
        # All runtime volumes must exist before any future `up` operation.
        expected_names = (
            [volume["name"] for volume in volumes.values()]
            + [volume["name"] for volume in vault_volumes.values()]
        )
        for name in sorted(set(expected_names)):
            details = json_command(["docker", "volume", "inspect", name])
            expected_owner = (VAULT_PROJECT if name.startswith(VAULT_PROJECT + "_")
                              else APP_PROJECT)
            require(isinstance(details, list) and len(details) == 1
                    and details[0].get("Name") == name
                    and (details[0].get("Labels") or {}).get(
                        "com.docker.compose.project") == expected_owner,
                    "Target runtime volume missing or has wrong owner: " + name)
        net = vault.get("networks", {}).get("vault_private", {})
        require(net.get("name") == self.network
                and net.get("external") is True,
                "Unexpected target Vault network")
        for name in ("vault", "vault-storage-permissions", "vault-tls-files"):
            require(name in vault_services and compose_mount(vault_services[name],
                    "/vault/tls", self.tls,
                    readonly=name == "vault"),
                    "Target Vault TLS mount differs: " + name)
        for name in VAULT_CA_SERVICES:
            require(name in vault_services and compose_mount(vault_services[name],
                    "/vault/tls/ca.crt", self.tls / "ca.crt"),
                    "Target Vault helper CA mount differs: " + name)
        self.ok("Vault runtime model uses original TLS, Raft and network")

    def run(self):
        self.paths()
        self.certificates()
        self.docker_identity()
        self.volumes_and_network()
        self.tls_mounts()
        self.target_models()
        if self.legacy:
            print("INFO: Legacy containers detected; unified cutover still required")
        print("RESULT: PREFLIGHT PASSED (read-only; runtime cutover not approved)")


def main():
    try:
        Preflight().run()
    except PreflightError as exc:
        print("FAIL: " + str(exc), file=sys.stderr)
        print("RESULT: PREFLIGHT FAILED (no changes made)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
