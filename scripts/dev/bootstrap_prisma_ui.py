#!/usr/bin/env python3
"""On-demand, local-only Prisma Studio; never provisions core services."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
# Direct Makefile invocation must also resolve scripts.dev package imports.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bootstrap_backend as backend
import bootstrap_backend_agent as agent
import bootstrap_postgres as pg
import bootstrap_unified as unified
import bootstrap_cli_progress as cli
import certificates
import configuration
from bootstrap_progress import Phase, Status

PROJECT = "transcendence-prisma-ui-dev"
COMPOSE = ROOT / "infra/prisma/ui/compose.yml"
GATEWAY_CONFIG = COMPOSE.with_name("haproxy.cfg")
NETWORK = backend.DEFAULT_NETWORK
IMAGE = "haproxy:3.2.27-alpine"
SERVICES = ("gateway", "studio")
LABELS = {
    "enable": ("Acquire startup lock", "Inspect existing UI", "Verify backend and image",
               "Start or keep UI", "Verify HTTPS"),
    "disable": ("Acquire startup lock", "Inspect existing UI", "Stop or keep UI", "Verify disabled state"),
    "status": ("Inspect existing UI", "Verify actual state"),
}


class StudioError(Exception):
    pass


def run(*args, env=None, timeout=40):
    try:
        result = subprocess.run(args, env=env, capture_output=True, text=True,
                                check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise StudioError("Local command unavailable or timed out") from None
    if result.returncode:
        raise StudioError(f"{args[0]} operation failed (exit {result.returncode}; output withheld)")
    return result.stdout


def local_docker():
    if os.geteuid() == 0 or any(os.environ.get(key) for key in ("DOCKER_HOST", "DOCKER_CONTEXT")):
        raise StudioError("Use the ordinary user and local Docker context without overrides")
    endpoint = json.loads(run("docker", "context", "inspect", "--format", "{{json .Endpoints.docker}}"))
    if endpoint.get("Host") != "unix:///var/run/docker.sock":
        raise StudioError("Expected local Docker Unix socket")


def inventory(port):
    items = {service: pg.exact_container(f"{PROJECT}-{service}-1") for service in SERVICES}
    # Do not act on a same-name foreign container or unrelated Compose project.
    for service, item in items.items():
        if item is not None:
            verify_identity(service, item, port)
    gateway, studio = (items[s] for s in SERVICES)
    if studio is not None and gateway is not None:
        if studio["HostConfig"].get("NetworkMode") != "container:" + gateway["Id"]:
            raise StudioError("Studio network namespace differs from the reviewed gateway")
    return items


def verify_identity(service, item, port):
    cfg, host = item.get("Config") or {}, item.get("HostConfig") or {}
    labels = cfg.get("Labels") or {}
    expected = {"project": PROJECT, "service": service,
                "project.config_files": str(COMPOSE), "project.working_dir": str(COMPOSE.parent)}
    if (item.get("Name") != f"/{PROJECT}-{service}-1"
            or any(labels.get("com.docker.compose." + k) != v for k, v in expected.items())
            or host.get("Privileged") is not False or host.get("ReadonlyRootfs") is not True
            or host.get("CapAdd") or set(host.get("CapDrop") or []) != {"ALL"}
            or not any(str(x).startswith("no-new-privileges") for x in host.get("SecurityOpt") or [])
            or (host.get("RestartPolicy") or {}).get("Name") != "no"):
        raise StudioError("Unexpected Prisma UI container identity or privileges")
    mounts = item.get("Mounts") or []
    if service == "gateway":
        ports = {"5556/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(port)}]}
        if (cfg.get("Image") != IMAGE or cfg.get("User") != "99:99"
                or "DEV_PRISMA_UI_PORT=" + str(port) not in (cfg.get("Env") or [])
                or set(host.get("GroupAdd") or []) != {"43043"}
                or host.get("PortBindings") != ports or len(mounts) != 2
                or not backend.required_mount(item, "/usr/local/etc/haproxy/haproxy.cfg",
                    kind="bind", source=GATEWAY_CONFIG, readonly=True)
                or not backend.required_mount(item, "/etc/haproxy/certs", kind="bind",
                    source=ROOT / "infra/nginx/certs", readonly=True)):
            raise StudioError("Unexpected Prisma gateway ports, image or mounts")
        if running(item):
            net = item.get("NetworkSettings") or {}
            if set(net.get("Networks") or {}) != {NETWORK} or net.get("Ports") != ports:
                raise StudioError("Unexpected running Prisma gateway network or ports")
    else:
        if (cfg.get("Image") not in (backend.EXPECTED_IMAGE, backend.EXPECTED_IMAGE + ":latest")
                or cfg.get("User") != "10001:20000" or host.get("PortBindings")
                or not str(host.get("NetworkMode", "")).startswith("container:")
                or cfg.get("Cmd") != ["/app/node_modules/.bin/prisma", "studio", "--port", "5555", "--browser", "none"]
                or len(mounts) != 1 or not backend.required_mount(item, "/run/secrets/backend",
                    kind="volume", source=agent.RENDERED_VOLUME, readonly=True)
                or "DATABASE_URL_FILE=/run/secrets/backend/database_url" not in (cfg.get("Env") or [])):
            raise StudioError("Unexpected Prisma Studio command, network or secret mount")
    if any(str(x).startswith(("DATABASE_URL=", "VAULT_TOKEN=", "POSTGRES_PASSWORD=", "PGPASSWORD="))
           for x in cfg.get("Env") or []):
        raise StudioError("Unexpected credential in Prisma UI environment")


def running(item):
    return (item is not None and (item.get("State") or {}).get("Running") is True
            and (item.get("State") or {}).get("Status") == "running")


def stopped(item):
    state = (item or {}).get("State") or {}
    return state.get("Running") is False and state.get("Status") in ("created", "exited")


def backend_image():
    item = backend.container()
    backend.verify_identity(item)
    if not backend.healthy(item):
        raise StudioError("Backend is not healthy; run make health")
    vault_agent = agent.inspect_agent()
    if vault_agent is None:
        raise StudioError("Backend Vault Agent is missing")
    agent.verify_identity(vault_agent)
    if not agent.ready(vault_agent):
        raise StudioError("Backend Vault Agent is not healthy")
    image = item["Config"]["Image"]
    actual = run("docker", "image", "inspect", "--format", "{{.Id}}", image).strip()
    if actual != item["Image"]:
        raise StudioError("Backend image tag differs from the deployed image; review the runtime")
    return image, actual


def verify_source():
    # Keep the reviewed two-file contract from Phase 1. Changes to either
    # source require reviewing the lifecycle guard as well.
    expected = {
        COMPOSE: "0513ac16b85346249a74e9817d5954ed702a876fe6a8840ff2f47bc7dbb890f3",
        GATEWAY_CONFIG: "0f94f2b3cf8c679f94a4bb97ee99bd36690640d16c006bb1d1388b2b10822344",
    }
    for file, digest in expected.items():
        if file.is_symlink() or hashlib.sha256(file.read_bytes()).hexdigest() != digest:
            raise StudioError("Prisma UI source differs from the reviewed configuration")


def compose_up(port, image):
    verify_source()
    env = {k: v for k, v in os.environ.items()
           if k in ("PATH", "HOME", "USER", "DOCKER_CONFIG", "XDG_RUNTIME_DIR")}
    env.update(DEV_PRISMA_UI_PORT=str(port), FT_PRISMA_BACKEND_IMAGE=image)
    run("docker", "compose", "--env-file", "/dev/null", "-p", PROJECT,
        "-f", str(COMPOSE), "up", "-d", "--no-build", "--pull", "never", "--no-recreate",
        "gateway", "studio", env=env, timeout=90)


def probe(port, *, attempts=1):
    ca = certificates.curl_ca_args()
    for i in range(attempts):
        try:
            status = run("curl", "-q", *ca, "--proto", "=https", "--noproxy", "*", "--resolve", f"localhost:{port}:127.0.0.1",
                         "--max-time", "5", "--silent", "--show-error", "--output", "/dev/null",
                         "--write-out", "%{http_code}", f"https://localhost:{port}/", timeout=7)
            if status == "200":
                return
        except StudioError:
            pass
        if i + 1 < attempts:
            time.sleep(1)
    raise StudioError("Prisma Studio HTTPS not ready; run make prisma-ui-disable before retrying")


def lifecycle(mode, port):
    with cli.stage("Inspect existing UI"):
        local_docker()
        items = inventory(port)
    if mode == "status":
        with cli.stage("Verify actual state") as step:
            if all(item is None or stopped(item) for item in items.values()):
                step.status = Status.CACHED
                return False
            if not all(running(item) for item in items.values()):
                raise StudioError("Partial or unsafe Prisma UI; run make prisma-ui-disable")
            verify_source()
            probe(port)
            return True
    if mode == "disable":
        with cli.stage("Stop or keep UI", Phase.PREPARE) as step:
            if any(item is not None and not (running(item) or stopped(item))
                   for item in items.values()):
                raise StudioError("Unexpected Prisma UI container state; review the runtime")
            if not any(running(item) for item in items.values()):
                step.status = Status.CACHED
            # Stop Studio before the owner of its network namespace.
            for service in reversed(SERVICES):
                item = items[service]
                if running(item):
                    run("docker", "stop", "--time", "10", item["Id"])
        with cli.stage("Verify disabled state"):
            after = inventory(port)
            for service, item in items.items():
                current = after[service]
                if item is None:
                    if current is not None:
                        raise StudioError("Prisma UI identity changed during disable")
                elif (current is None or current["Id"] != item["Id"]
                      or not stopped(current)):
                    raise StudioError("Cannot verify retained stopped Prisma UI container")
        return False
    with cli.stage("Verify backend and image"):
        verify_source()
        image, image_id = backend_image()
    with cli.stage("Start or keep UI", Phase.PREPARE) as step:
        if any(items.values()):
            if not all(items.values()):
                raise StudioError("Incomplete Prisma UI pair; disable and manually remove retained UI containers before retrying")
            if items["studio"]["Image"] != image_id:
                raise StudioError("Outdated Prisma Studio image; disable and manually remove both UI containers before retrying")
            if all(running(item) for item in items.values()):
                step.status = Status.CACHED
            elif all(stopped(item) for item in items.values()):
                # Start the network namespace owner before its dependent Studio.
                for service in SERVICES:
                    run("docker", "start", items[service]["Id"])
            else:
                raise StudioError("Partial or unsafe Prisma UI; disable then enable")
        else:
            compose_up(port, image)
    with cli.stage("Verify HTTPS"):
        after = inventory(port)
        if not all(running(item) for item in after.values()) or after["studio"]["Image"] != image_id:
            raise StudioError("Prisma UI startup incomplete; disable before retrying")
        if any(item is not None and after[service]["Id"] != item["Id"]
               for service, item in items.items()):
            raise StudioError("Prisma UI identity changed during enable")
        probe(port, attempts=15)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=LABELS)
    args = parser.parse_args()
    try:
        if os.geteuid() == 0:
            raise StudioError("Run as an ordinary user, not root")
        public = configuration.load()
        with cli.presentation(LABELS[args.mode], f"{args.mode.capitalize()} Prisma Studio"):
            if args.mode == "status":
                enabled = lifecycle(args.mode, public.prisma_ui_port)
            else:
                with ExitStack() as stack:
                    with cli.stage("Acquire startup lock"):
                        stack.enter_context(unified.exclusive_orchestrator_lock())
                    enabled = lifecycle(args.mode, public.prisma_ui_port)
        print("STATUS: PRISMA UI ENABLED (HTTPS ready)" if enabled else "STATUS: PRISMA UI DISABLED")
        if enabled:
            print("Prisma Studio: " + public.prisma_ui_url)
        return 0
    except (StudioError, unified.UnifiedStartupError, configuration.ConfigurationError,
            pg.PostgresBootstrapError, backend.BackendBootstrapError, agent.BackendAgentError,
            certificates.LocalCAError, OSError, ValueError, KeyError, TypeError):
        # Never print Docker inspection, subprocess output or secret-bearing exceptions.
        exc = sys.exc_info()[1]
        message = str(exc) if isinstance(exc, StudioError) else "Prisma UI prerequisite or identity check failed"
        print("ERROR: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
