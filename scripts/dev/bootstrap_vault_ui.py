#!/usr/bin/env python3
"""Guarded local Vault Web UI gateway bootstrap."""

if __package__:
    from . import bootstrap_plan_events as plan_events
else:
    import bootstrap_plan_events as plan_events

import argparse
import json
import os
from pathlib import Path
import stat
import ssl
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "transcendence-vault-ui-dev"
SERVICE = "gateway"
NAME = f"{PROJECT}-{SERVICE}-1"
COMPOSE = ROOT / "infra/vault/ui/compose.yml"
CONFIG = ROOT / "infra/vault/ui/haproxy.cfg"
IMAGE = "haproxy:3.2.27-alpine"
PRIVATE = "transcendence-vault-dev_vault_private"
EDGE = f"{PROJECT}_gateway_edge"
CA = (
    Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    / "transcendence/dev/tls/ca.crt"
)
TARGET = "/usr/local/etc/haproxy/haproxy.cfg"
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "transcendence/dev"
ENABLED = STATE / "vault-ui.enabled"

EXPECTED_CONFIG = """global
    maxconn 64

defaults
    mode tcp
    timeout connect 5s
    timeout client 2m
    timeout server 2m

frontend vault_ui
    bind :8200
    default_backend vault_server

backend vault_server
    server vault vault:8200 check
"""


class GatewayError(Exception):
    pass


def command(args, *, timeout=45, env=None):
    try:
        result = subprocess.run(
            args, capture_output=True, text=True,
            check=False, timeout=timeout, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise GatewayError("Docker command unavailable or timed out") from None

    if result.returncode != 0:
        raise GatewayError("Docker operation failed; details withheld")

    return result.stdout


def docker_json(*args):
    try:
        return json.loads(command(["docker", *args]))
    except (ValueError, TypeError):
        raise GatewayError("Invalid Docker JSON") from None


def verify_source():
    for path in (COMPOSE, CONFIG):
        if not path.is_file() or path.is_symlink():
            raise GatewayError("Missing or unsafe gateway source file")

    if CONFIG.read_text() != EXPECTED_CONFIG:
        raise GatewayError("Unreviewed HAProxy configuration")


def compose():
    verify_source()

    env = {
        k: v for k, v in os.environ.items()
        if k in (
            "PATH", "HOME", "USER", "DOCKER_HOST",
            "DOCKER_CONTEXT", "DOCKER_CONFIG", "XDG_RUNTIME_DIR",
        )
    }

    args = [
        "docker", "compose", "--env-file", "/dev/null",
        "-p", PROJECT, "-f", str(COMPOSE),
    ]

    raw = command(args + ["config", "--format", "json"], env=env)

    try:
        model = json.loads(raw)
        service = model["services"][SERVICE]
    except (ValueError, TypeError, KeyError):
        raise GatewayError("Invalid gateway Compose model") from None

    networks = model.get("networks") or {}
    ports = service.get("ports") or []
    volumes = service.get("volumes") or []

    if (
        model.get("name") != PROJECT
        or set(model.get("services") or {}) != {SERVICE}
        or service.get("image") != IMAGE
        or str(service.get("user")) != "99:99"
        or service.get("read_only") is not True
        or set(service.get("networks") or {}) != {
            "gateway_edge", "vault_private"
        }
        or set(service.get("cap_drop") or []) != {"ALL"}
        or "no-new-privileges:true" not in (
            service.get("security_opt") or []
        )
        or service.get("restart") != "no"
        or len(ports) != 1
        or len(volumes) != 1
    ):
        raise GatewayError("Unexpected gateway Compose security contract")

    port = ports[0]
    volume = volumes[0]

    if (
        port.get("host_ip") != "127.0.0.1"
        or str(port.get("published")) != "8200"
        or port.get("target") != 8200
        or port.get("protocol", "tcp") != "tcp"
        or volume.get("type") != "bind"
        or volume.get("source") != str(CONFIG)
        or volume.get("target") != TARGET
        or volume.get("read_only") is not True
        or set(networks) != {"gateway_edge", "vault_private"}
        or networks["gateway_edge"].get("name") != EDGE
        or networks["gateway_edge"].get("driver") != "bridge"
        or networks["gateway_edge"].get("external") is True
        or networks["vault_private"].get("name") != PRIVATE
        or networks["vault_private"].get("external") is not True
    ):
        raise GatewayError("Unexpected gateway ports, mounts or networks")

    forbidden = (
        "privileged", "cap_add", "devices", "secrets",
        "environment", "entrypoint", "command", "network_mode",
        "pid", "ipc", "extra_hosts",
    )
    if any(service.get(key) for key in forbidden):
        raise GatewayError("Unexpected gateway Compose privileges")

    return args, env


def inspect():
    names = command([
        "docker", "ps", "-a",
        "--filter", f"name=^/{NAME}$",
        "--format", "{{.Names}}",
    ]).splitlines()

    if not names:
        return None
    if names != [NAME]:
        raise GatewayError("Ambiguous gateway container identity")

    data = docker_json("container", "inspect", NAME)
    if not isinstance(data, list) or len(data) != 1:
        raise GatewayError("Unexpected gateway inspect response")
    return data[0]


def verify_network(name, project, internal):
    data = docker_json("network", "inspect", name)
    if not isinstance(data, list) or len(data) != 1:
        raise GatewayError("Ambiguous gateway network")

    network = data[0]
    labels = network.get("Labels") or {}

    if (
        network.get("Name") != name
        or network.get("Driver") != "bridge"
        or network.get("Internal") is not internal
        or labels.get("com.docker.compose.project") != project
    ):
        raise GatewayError("Unexpected Docker network identity")


def verify_identity(item):
    cfg = item.get("Config") or {}
    host = item.get("HostConfig") or {}
    network = item.get("NetworkSettings") or {}
    labels = cfg.get("Labels") or {}

    expected_labels = {
        "com.docker.compose.project": PROJECT,
        "com.docker.compose.service": SERVICE,
        "com.docker.compose.project.config_files": str(COMPOSE),
        "com.docker.compose.project.working_dir": str(COMPOSE.parent),
    }

    port = {
        "8200/tcp": [
            {"HostIp": "127.0.0.1", "HostPort": "8200"}
        ]
    }

    if (
        item.get("Name") != "/" + NAME
        or any(labels.get(k) != v for k, v in expected_labels.items())
        or cfg.get("Image") != IMAGE
        or cfg.get("User") != "99:99"
        or host.get("Privileged") is not False
        or host.get("ReadonlyRootfs") is not True
        or set(host.get("CapDrop") or []) != {"ALL"}
        or host.get("CapAdd")
        or "no-new-privileges:true" not in (
            host.get("SecurityOpt") or []
        )
        or host.get("PortBindings") != port
        or host.get("Tmpfs") != {
            "/tmp": "rw,noexec,nosuid,size=16m"
        }
        or (host.get("RestartPolicy") or {}).get("Name") != "no"
        or set((network.get("Networks") or {})) != {PRIVATE, EDGE}
    ):
        raise GatewayError("Unexpected gateway container privileges or identity")

    state = item.get("State") or {}
    if state.get("Running") is True and network.get("Ports") != port:
        raise GatewayError("Actual published ports differ")

    mounts = item.get("Mounts") or []
    if len(mounts) != 1:
        raise GatewayError("Unexpected gateway mounts")

    mount = mounts[0]
    if (
        mount.get("Type") != "bind"
        or mount.get("Source") != str(CONFIG)
        or mount.get("Destination") != TARGET
        or mount.get("RW") is not False
    ):
        raise GatewayError("Unexpected HAProxy configuration mount")

    forbidden = (
        "VAULT_TOKEN=", "DATABASE_URL=", "POSTGRES_PASSWORD=",
        "GF_SECURITY_ADMIN_PASSWORD=",
    )
    if any(
        str(value).startswith(forbidden)
        for value in cfg.get("Env") or []
    ):
        raise GatewayError("Credentials found in gateway environment")


def desired_enabled():
    """Persistent operator intent; absent marker means intentionally disabled.

    Invalid or unsafe state NEVER counts as disabled.
    """
    if STATE.is_symlink():
        raise GatewayError("Unsafe Vault UI state directory")
    if not STATE.exists():
        return False
    parent = STATE.stat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.getuid()
        or parent.st_mode & 0o077
    ):
        raise GatewayError("Unsafe Vault UI state directory")
    try:
        fd = os.open(ENABLED, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return False
    except OSError:
        raise GatewayError("Cannot safely read Vault UI intent") from None
    try:
        meta = os.fstat(fd)
        if (
            not stat.S_ISREG(meta.st_mode)
            or meta.st_uid != os.getuid()
            or stat.S_IMODE(meta.st_mode) != 0o600
            or os.read(fd, 32) != b"enabled\n"
        ):
            raise GatewayError("Untrusted Vault UI intent marker")
    finally:
        os.close(fd)
    return True


def mark_enabled():
    STATE.mkdir(parents=True, mode=0o700, exist_ok=True)
    if desired_enabled():
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(ENABLED, flags, 0o600)
    except OSError:
        raise GatewayError("Cannot safely enable Vault UI intent") from None
    with os.fdopen(fd, "wb") as output:
        output.write(b"enabled\n")
        output.flush()
        os.fsync(output.fileno())


def mark_disabled():
    if desired_enabled():
        try:
            ENABLED.unlink()
        except OSError:
            raise GatewayError("Cannot clear Vault UI intent") from None


def check_disabled():
    """Disabled is healthy only when the gateway really cannot serve UI."""
    compose()
    item = inspect()
    if item is not None:
        verify_identity(item)
        verify_network(PRIVATE, "transcendence-vault-dev", True)
        verify_network(EDGE, PROJECT, False)
        state = item.get("State") or {}
        if state.get("Running") is not False or state.get("Status") not in ("exited", "created"):
            raise GatewayError("Vault UI unexpectedly running or in an unsafe state")
    print("RESULT: VAULT UI DISABLED (expected, read-only)")


def status():
    if desired_enabled():
        check()
        print("STATUS: VAULT UI ENABLED (healthy)")
    else:
        check_disabled()
        print("STATUS: VAULT UI DISABLED (expected)")


def enable():
    # Validate the exact Compose model and any existing gateway BEFORE
    # writing operator intent. A failed subsequent start stays enabled/failed.
    compose()
    item = inspect()
    if item is not None:
        verify_identity(item)
    mark_enabled()
    apply()
    print("RESULT: VAULT UI ENABLED")


def disable():
    # Stop only the known, fully verified gateway, never Vault itself.
    compose()
    item = inspect()
    if item is not None:
        verify_identity(item)
        verify_network(PRIVATE, "transcendence-vault-dev", True)
        verify_network(EDGE, PROJECT, False)
        state = item.get("State") or {}
        if state.get("Running") is True and state.get("Status") == "running":
            old_id = item.get("Id")
            command(["docker", "stop", "--time", "10", NAME], timeout=35)
            after = inspect()
            if after is None or after.get("Id") != old_id:
                raise GatewayError("Gateway identity changed during stop")
            verify_identity(after)
            state = after.get("State") or {}
        if state.get("Running") is not False or state.get("Status") not in ("created", "exited"):
            raise GatewayError("Cannot verify that Vault UI is stopped")
    # Clear intent only after stop is verified; failures stay visible to health.
    mark_disabled()
    check_disabled()
    print("RESULT: VAULT UI DISABLED")


def probe_ui():
    if not CA.is_file() or CA.is_symlink():
        raise GatewayError("Vault CA certificate missing")

    context = ssl.create_default_context(cafile=str(CA))
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
    )

    try:
        with opener.open(
            "https://localhost:8200/ui/", timeout=12
        ) as response:
            body = response.read(8192)
            if (
                response.status != 200
                or response.headers.get_content_type() != "text/html"
                or len(body) < 1024
                or b"<html" not in body.lower()
            ):
                raise GatewayError("Vault Web UI response is invalid")
    except (OSError, ValueError):
        raise GatewayError("Vault Web UI TLS/HTTP probe failed") from None


def check():
    compose()

    item = inspect()
    if item is None:
        raise GatewayError("Gateway container missing")

    verify_identity(item)
    verify_network(PRIVATE, "transcendence-vault-dev", True)
    verify_network(EDGE, PROJECT, False)

    state = item.get("State") or {}
    if state.get("Running") is not True:
        raise GatewayError("Gateway is not running")

    probe_ui()
    print("RESULT: VAULT UI GATEWAY READY (read-only)")


def apply():
    args, env = compose()

    command([
        sys.executable,
        str(ROOT / "scripts/dev/bootstrap_vault.py"),
        "check",
    ])

    verify_network(PRIVATE, "transcendence-vault-dev", True)

    item = inspect()
    if item is not None:
        verify_identity(item)

        status = (item.get("State") or {}).get("Status")

        if status == "running":
            print(f"KEEP: {NAME}", flush=True)
        elif status in ("created", "exited"):
            original = item.get("Id")
            command(["docker", "start", NAME], timeout=90)

            updated = inspect()
            if updated is None or updated.get("Id") != original:
                raise GatewayError("Gateway identity changed")
            verify_identity(updated)
            print(f"START: {NAME}", flush=True)
        else:
            raise GatewayError("Unexpected gateway state")
    else:
        command(
            args + [
                "up", "-d", "--no-deps",
                "--no-recreate", SERVICE,
            ],
            timeout=180,
            env=env,
        )
        print(f"CREATED: {NAME}", flush=True)

    check()
    print("RESULT: VAULT UI GATEWAY READY (apply)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "check", "apply", "enable", "disable", "status"))
    args = parser.parse_args()

    try:
        if os.geteuid() == 0:
            raise GatewayError("Do not run as root")

        if args.mode == "plan":
            compose()
            item = inspect()
            if item is None:
                plan_events.container("Vault UI gateway", item, optional=True)
                print("TODO: Vault UI gateway not created (read-only)")
            else:
                verify_identity(item)
                plan_events.container("Vault UI gateway", item, optional=True)
                print("EXISTS: Vault UI gateway (read-only)")
            return 0

        if args.mode == "check":
            if not desired_enabled():
                raise GatewayError("Vault UI is intentionally disabled")
            check()
        elif args.mode == "status":
            status()
        else:
            # Shared startup lock prevents a concurrent make start/initialize
            # from racing the explicit administrative lifecycle transition.
            import bootstrap_unified as unified
            try:
                with unified.exclusive_orchestrator_lock():
                    if args.mode == "disable":
                        disable()
                    else:
                        enable()  # Legacy `apply` also records enabled intent.
            except unified.UnifiedStartupError:
                raise GatewayError("Trusted startup lock unavailable") from None
        return 0

    except GatewayError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("RESULT: VAULT UI GATEWAY REFUSED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
