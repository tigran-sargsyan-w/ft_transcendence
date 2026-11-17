#!/usr/bin/env python3
"""Phase 5C: start only Docker Socket Proxy, Collector and Graph Engine.

Requires the already verified Vault-backed NestJS backend. Never recreates
existing containers, changes volumes, provisions credentials or starts Nginx.
All published health checks are executed inside trusted running containers;
no Docker socket, service port or secret is exposed on the host.
"""

import argparse
import fcntl
import subprocess
import sys
import time

import bootstrap_backend as backend
import bootstrap_backend_agent as agent
import bootstrap_postgres as pg

PROJECT = pg.PROJECT
DEFAULT = "transcendence-app-dev_default"
CONTROL = "transcendence-app-dev_collector_control"
SERVICES = ("docker-proxy", "collector", "graph-engine")
IMAGES = {
    "docker-proxy": ("tecnativa/docker-socket-proxy:latest",),
    "collector": ("transcendence-app-dev-collector", "transcendence-app-dev-collector:latest"),
    "graph-engine": ("transcendence-app-dev-graph-engine", "transcendence-app-dev-graph-engine:latest"),
}
NETWORKS = {
    "docker-proxy": {CONTROL},
    "collector": {CONTROL, DEFAULT},
    "graph-engine": {DEFAULT},
}

# A real local HTTP request from the collector proves that Docker Proxy GET
# endpoints work and its snapshot endpoint can enumerate current resources.
COLLECTOR_HTTP = r"""
(async () => {
  const get = async (path) => {
    const response = await fetch('http://127.0.0.1:3001' + path, {
      signal: AbortSignal.timeout(12000),
    });
    if (!response.ok) throw new Error('HTTP status');
    return response.json();
  };
  const health = await get('/api/v1/health');
  if (health?.data?.status !== 'ok' ||
      health?.data?.docker?.status !== 'ok') throw new Error('collector health');
  const snapshot = await get('/api/v1/snapshot');
  if (snapshot?.data?.schemaVersion !== 1 ||
      snapshot?.data?.environmentId !== 'env_local_compose' ||
      !Array.isArray(snapshot?.data?.containers) ||
      !Array.isArray(snapshot?.data?.networks) ||
      !Array.isArray(snapshot?.data?.volumes)) throw new Error('collector snapshot');
})().catch(() => { process.exitCode = 1; });
"""

# Check the two internal URLs from the existing backend network namespace.
# No response body or environment values are printed to stdout/stderr.
BACKEND_REACHABILITY = r"""
(async () => {
  const targets = [
    ['http://collector:3001/api/v1/health', 'ok'],
    ['http://graph-engine:8000/api/v1/health', 'ok'],
  ];
  for (const [url, expected] of targets) {
    const response = await fetch(url, { signal: AbortSignal.timeout(7000) });
    if (!response.ok) throw new Error('unreachable service');
    const body = await response.json();
    if (body?.data?.status !== expected) throw new Error('bad service health');
  }
})().catch(() => { process.exitCode = 1; });
"""

GRAPH_HEALTH = (
    "import urllib.request,json; "
    "r=urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=5); "
    "assert r.status == 200 and json.load(r)['data']['status'] == 'ok'"
)


class SupportServicesError(Exception):
    """Diagnostic text excludes subprocess output and environment secrets."""


def run(argv, *, env=None, timeout=90):
    try:
        result = subprocess.run(
            argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SupportServicesError(
            f"Support service command unavailable/timed out: {argv[0]}"
        ) from None
    if result.returncode:
        raise SupportServicesError(
            f"Support service command failed: {argv[0]} (exit {result.returncode}; output withheld)"
        )
    return result


def name(service):
    if service not in SERVICES:
        raise SupportServicesError("Unexpected support service")
    return f"{PROJECT}-{service}-1"


def inspect(service):
    return pg.exact_container(name(service))


def require_environment(env, expected):
    pairs = dict(pair.split("=", 1) for pair in env if "=" in pair)
    return all(pairs.get(k) == v for k, v in expected.items())


def verify_identity(service, item):
    """Validate existing container before any start or check operation."""
    if service not in SERVICES or not isinstance(item, dict):
        raise SupportServicesError("Invalid support container identity")
    cfg = item.get("Config") or {}
    labels = cfg.get("Labels") or {}
    host = item.get("HostConfig") or {}
    expected_labels = {
        "com.docker.compose.project": PROJECT,
        "com.docker.compose.service": service,
        "com.docker.compose.project.config_files": str(pg.COMPOSE_FILE),
        "com.docker.compose.project.working_dir": str(pg.ROOT),
    }
    if (item.get("Name") != "/" + name(service)
            or any(labels.get(k) != v for k, v in expected_labels.items())
            or cfg.get("Image") not in IMAGES[service]):
        raise SupportServicesError(f"Unexpected Compose identity: {service}")

    nets = (item.get("NetworkSettings") or {}).get("Networks") or {}
    if (set(nets) != NETWORKS[service]
            or host.get("Privileged") is not False
            or host.get("PortBindings")):
        raise SupportServicesError(f"Unexpected networks/privileges: {service}")

    env = cfg.get("Env") or []
    if any(str(e).startswith((
            "DATABASE_URL=", "VAULT_TOKEN=", "POSTGRES_PASSWORD=", "PGPASSWORD=",
            "DATABASE_URL_FILE=",
    )) for e in env):
        raise SupportServicesError(f"Unexpected credentials in environment: {service}")

    mounts = item.get("Mounts") or []
    if service == "docker-proxy":
        if len(mounts) != 1 or not backend.required_mount(
                item, "/var/run/docker.sock", kind="bind",
                source="/var/run/docker.sock", readonly=True):
            raise SupportServicesError("Docker socket proxy has unexpected mounts")
        if not require_environment(env, {
            "POST": "0", "CONTAINERS": "1", "NETWORKS": "1",
            "VOLUMES": "1", "EVENTS": "1", "PING": "1", "VERSION": "1",
        }):
            raise SupportServicesError("Docker socket proxy API permissions unexpected")
    elif mounts:
        raise SupportServicesError(f"Unexpected mounts: {service}")
    elif service == "collector" and not require_environment(env, {
            "PORT": "3001", "DOCKER_API_URL": "http://docker-proxy:2375",
            "ENVIRONMENT_ID": "env_local_compose",
    }):
        raise SupportServicesError("Collector configuration unexpected")
    return item


def running(item):
    state = item.get("State") or {}
    return state.get("Running") is True and state.get("Status") == "running"


def wait_running(service, attempts=60, delay=2):
    for attempt in range(attempts):
        item = inspect(service)
        if item is None:
            raise SupportServicesError(f"Support container disappeared: {service}")
        verify_identity(service, item)
        state = item.get("State") or {}
        if running(item):
            if service != "graph-engine" or (state.get("Health") or {}).get("Status") == "healthy":
                return
        if state.get("Status") in ("exited", "dead") or (
                (state.get("Health") or {}).get("Status") == "unhealthy"):
            raise SupportServicesError(f"Support container stopped/unhealthy: {service}")
        if attempt + 1 < attempts:
            time.sleep(delay)
    raise SupportServicesError(f"Support service health timeout: {service}")


def probe_services():
    run(["docker", "exec", name("collector"), "node", "-e", COLLECTOR_HTTP], timeout=40)
    print("PASS: Collector health, snapshot and Docker Proxy GET access", flush=True)
    run(["docker", "exec", name("graph-engine"), "python", "-c", GRAPH_HEALTH], timeout=20)
    print("PASS: Graph Engine HTTP health", flush=True)
    run(["docker", "exec", backend.NAME, "node", "-e", BACKEND_REACHABILITY], timeout=35)
    print("PASS: Backend can reach Collector and Graph Engine", flush=True)


def require_prerequisites():
    if backend.main(["check"]) != 0:
        raise SupportServicesError("Vault-backed NestJS backend is not ready")


def start_or_keep(service, env):
    item = inspect(service)
    if item is not None:
        verify_identity(service, item)
        status = (item.get("State") or {}).get("Status")
        if status == "running":
            print(f"KEEP: {name(service)}", flush=True)
        elif status in ("created", "exited"):
            run(["docker", "start", name(service)], timeout=90)
            print(f"START: {name(service)}", flush=True)
        else:
            raise SupportServicesError(f"Unexpected state for {service}")
    else:
        build = ["--build"] if service in ("collector", "graph-engine") else []
        run(agent.app_compose(
            "up", "-d", "--no-deps", "--no-recreate", *build, service,
        ), env=env, timeout=1200)
        item = inspect(service)
        if item is None:
            raise SupportServicesError(f"Compose did not create {service}")
        verify_identity(service, item)
        print(f"CREATED: {name(service)}", flush=True)
    wait_running(service)


def check():
    require_prerequisites()
    for service in SERVICES:
        item = inspect(service)
        if item is None:
            raise SupportServicesError(f"Missing support service: {service}")
        verify_identity(service, item)
        if not running(item):
            raise SupportServicesError(f"Support service not running: {service}")
        if service == "graph-engine" and (item.get("State", {}).get("Health") or {}).get("Status") != "healthy":
            raise SupportServicesError("Graph Engine not healthy")
    probe_services()
    print("RESULT: SUPPORT SERVICES READY (read-only)")


def apply():
    with pg.LOCK.open("rb") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SupportServicesError("Another startup/migration workflow is running") from None
        require_prerequisites()
        # Validate every previously created container before touching ANY.
        for service in SERVICES:
            item = inspect(service)
            if item is not None:
                verify_identity(service, item)
        env = agent.safe_env()
        for service in SERVICES:
            start_or_keep(service, env)
        probe_services()
    print("RESULT: SUPPORT SERVICES READY (apply)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "check", "apply"))
    args = parser.parse_args(argv)
    try:
        if args.mode == "plan":
            for service in SERVICES:
                item = inspect(service)
                if item is None:
                    print(f"TODO: {service} not created (read-only)")
                else:
                    verify_identity(service, item)
                    state = item.get("State") or {}
                    print(f"EXISTS: {service} ({state.get('Status')})")
            print("RESULT: SUPPORT SERVICES PLAN COMPLETE (read-only)")
            return 0
        if args.mode == "check":
            check()
            return 0
        apply()
        return 0
    except (SupportServicesError, backend.BackendBootstrapError,
            agent.BackendAgentError, pg.PostgresBootstrapError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("RESULT: SUPPORT SERVICES STOPPED (no resources removed)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
