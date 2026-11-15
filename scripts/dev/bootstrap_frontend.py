#!/usr/bin/env python3
"""Phase 5D: start only the existing React/Vite development frontend.

Requires the verified backend and three support services. Builds a missing
Compose frontend without dependency recreation, or resumes a trusted stopped
container. Performs real Vite HTML/module and backend-proxy HTTP probes.
No Nginx, Vault, PostgreSQL, secrets or other services are modified.
"""

import argparse
import fcntl
import sys
import time

import bootstrap_backend_agent as agent
import bootstrap_postgres as pg
import bootstrap_support_services as support

NAME = f"{pg.PROJECT}-frontend-1"
SERVICE = "frontend"
IMAGES = (f"{pg.PROJECT}-frontend", f"{pg.PROJECT}-frontend:latest")
DEFAULT_NETWORK = f"{pg.PROJECT}_default"

# HTTP request is executed inside the frontend container. Vite does not have a
# Compose healthcheck: testing only State.Running is insufficient. Verify that
# the HTML, transformed entry module AND existing /api reverse proxy work.
# Neither response bodies nor credentials are printed or persisted.
FRONTEND_HTTP = r"""
(async () => {
  const get = async (path) => {
    const response = await fetch('http://127.0.0.1:5173' + path, {
      headers: { Host: 'transcendence.test' },
      signal: AbortSignal.timeout(7000),
    });
    if (response.status !== 200) throw new Error('HTTP response not 200');
    return response;
  };
  const html = await (await get('/')).text();
  if (!html.includes('id="root"') || !html.includes('/src/main.tsx'))
    throw new Error('Unexpected Vite entry HTML');
  const js = await (await get('/src/main.tsx')).text();
  if (!js.includes('createRoot')) throw new Error('Vite entry module unavailable');
  const health = await (await get('/api/v1/health')).json();
  if (health?.data?.status !== 'ok' || health?.data?.database !== 'ok')
    throw new Error('Frontend backend proxy health failed');
})().catch(() => { process.exitCode = 1; });
"""


class FrontendBootstrapError(Exception):
    """Public errors must not include subprocess output or secrets."""


def container():
    return pg.exact_container(NAME)


def verify_identity(item):
    """Validate the existing Compose container before start or inspection."""
    if not isinstance(item, dict):
        raise FrontendBootstrapError("Invalid frontend container inspect result")
    cfg = item.get("Config") or {}
    labels = cfg.get("Labels") or {}
    host = item.get("HostConfig") or {}
    expected_labels = {
        "com.docker.compose.project": pg.PROJECT,
        "com.docker.compose.service": SERVICE,
        "com.docker.compose.project.config_files": str(pg.COMPOSE_FILE),
        "com.docker.compose.project.working_dir": str(pg.ROOT),
    }
    if (item.get("Name") != "/" + NAME
            or any(labels.get(k) != v for k, v in expected_labels.items())
            or cfg.get("Image") not in IMAGES):
        raise FrontendBootstrapError("Unexpected React/Vite Compose identity")

    networks = (item.get("NetworkSettings") or {}).get("Networks") or {}
    if (set(networks) != {DEFAULT_NETWORK}
            or host.get("Privileged") is not False
            or host.get("PortBindings")
            or item.get("Mounts")):
        raise FrontendBootstrapError("Unexpected frontend networks, ports or mounts")

    entries = cfg.get("Env") or []
    if ("BACKEND_URL=http://backend:3000" not in entries
            or any(str(entry).startswith((
                "DATABASE_URL=", "DATABASE_URL_FILE=", "VAULT_TOKEN=",
                "POSTGRES_PASSWORD=", "PGPASSWORD=",
            )) for entry in entries)):
        raise FrontendBootstrapError("Unexpected frontend environment configuration")
    return item


def ready(item):
    state = item.get("State") or {}
    return state.get("Running") is True and state.get("Status") == "running"


def probe_http():
    support.run(["docker", "exec", NAME, "node", "-e", FRONTEND_HTTP], timeout=35)
    print("PASS: React/Vite HTML, entry module and backend API proxy", flush=True)


def wait_frontend(attempts=60, delay=2):
    for attempt in range(attempts):
        item = container()
        if item is None:
            raise FrontendBootstrapError("Frontend container disappeared")
        verify_identity(item)
        state = item.get("State") or {}
        if ready(item):
            try:
                probe_http()
                return
            except support.SupportServicesError:
                pass
        if state.get("Status") in ("exited", "dead"):
            raise FrontendBootstrapError("Frontend stopped; inspect local Docker logs")
        if attempt + 1 < attempts:
            time.sleep(delay)
    raise FrontendBootstrapError("React/Vite frontend HTTP timeout (no cleanup attempted)")


def require_prerequisites():
    if support.main(["check"]) != 0:
        raise FrontendBootstrapError("Backend or support services are not ready")


def start_or_keep(env):
    item = container()
    if item is not None:
        verify_identity(item)
        state = (item.get("State") or {}).get("Status")
        if state == "running":
            print(f"KEEP: {NAME}", flush=True)
        elif state in ("created", "exited"):
            support.run(["docker", "start", NAME], timeout=90)
            print(f"START: {NAME}", flush=True)
        else:
            raise FrontendBootstrapError("Unexpected existing frontend state")
        return

    support.run(agent.app_compose(
        "up", "-d", "--no-deps", "--no-recreate", "--build", SERVICE,
    ), env=env, timeout=1200)
    item = container()
    if item is None:
        raise FrontendBootstrapError("React/Vite container was not created")
    verify_identity(item)
    print(f"CREATED: {NAME}", flush=True)


def apply():
    with pg.LOCK.open("rb") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise FrontendBootstrapError("Another startup or migration workflow is running") from None
        require_prerequisites()
        # Never recreate a container with unexpected provenance or privileges.
        existing = container()
        if existing is not None:
            verify_identity(existing)
        start_or_keep(agent.safe_env())
        wait_frontend()
    print("RESULT: REACT FRONTEND READY (apply)", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "check", "apply"))
    args = parser.parse_args(argv)
    try:
        if args.mode == "plan":
            item = container()
            if item is None:
                print("TODO: React frontend not created (read-only)")
            else:
                verify_identity(item)
                print(f"EXISTS: React frontend ({(item.get('State') or {}).get('Status')})")
            print("RESULT: REACT FRONTEND PLAN COMPLETE (read-only)")
            return 0
        if args.mode == "check":
            require_prerequisites()
            item = container()
            if item is None:
                raise FrontendBootstrapError("React frontend container missing")
            verify_identity(item)
            if not ready(item):
                raise FrontendBootstrapError("React frontend is not running")
            probe_http()
            print("RESULT: REACT FRONTEND READY (read-only)", flush=True)
            return 0
        apply()
        return 0
    except (FrontendBootstrapError, support.SupportServicesError,
            pg.PostgresBootstrapError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("RESULT: REACT FRONTEND STOPPED (no resources removed)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
