#!/usr/bin/env python3
"""Phase 5E: first-start and verify the trusted Nginx HTTPS gateway.

Requires Phase 5D frontend and all previously verified services. Never
recreates existing containers; does not modify TLS certificates, credentials,
volumes, Vault or PostgreSQL. Publishes only loopback 8080/8443.
"""

import argparse
import fcntl
import json
import subprocess
import sys
import time
from pathlib import Path

import bootstrap_backend_agent as agent
import bootstrap_frontend as frontend
import bootstrap_postgres as pg
import bootstrap_support_services as support

NAME = f"{pg.PROJECT}-nginx-1"
SERVICE = "nginx"
IMAGE = "nginx:1.28-alpine"
DEFAULT_NETWORK = f"{pg.PROJECT}_default"
DOMAIN = "transcendence.test"
CERTS = pg.ROOT / "infra/nginx/certs"
CONFIG = pg.ROOT / "infra/nginx/default.conf"
PORTS = {
    "80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}],
    "443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8443"}],
}
FORBIDDEN_ENV = (
    "DATABASE_URL=", "DATABASE_URL_FILE=", "VAULT_TOKEN=",
    "POSTGRES_PASSWORD=", "PGPASSWORD=", "VAULT_SECRET_ID=",
)


class NginxBootstrapError(Exception):
    """Public errors exclude Docker command output and HTTP response bodies."""


def run(argv, *, env=None, timeout=30):
    try:
        result = subprocess.run(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, check=False, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise NginxBootstrapError(f"Nginx command unavailable or timed out: {argv[0]}") from None
    if result.returncode != 0:
        raise NginxBootstrapError(
            f"Nginx command failed: {argv[0]} (exit {result.returncode}; output withheld)"
        )
    return result.stdout


def container():
    return pg.exact_container(NAME)


def verify_identity(item):
    if not isinstance(item, dict):
        raise NginxBootstrapError("Invalid Nginx Docker inspection")
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
            or cfg.get("Image") != IMAGE):
        raise NginxBootstrapError("Unexpected Nginx Compose container identity")

    networks = (item.get("NetworkSettings") or {}).get("Networks") or {}
    if (set(networks) != {DEFAULT_NETWORK}
            or host.get("Privileged") is not False
            or host.get("PortBindings") != PORTS
            or (host.get("RestartPolicy") or {}).get("Name") != "unless-stopped"):
        raise NginxBootstrapError("Unexpected Nginx network, ports or privileges")

    mounts = item.get("Mounts") or []
    if (len(mounts) != 2 or not support.backend.required_mount(
            item, "/etc/nginx/conf.d/default.conf", kind="bind",
            source=CONFIG, readonly=True)
            or not support.backend.required_mount(
            item, "/etc/nginx/certs", kind="bind",
            source=CERTS, readonly=True)):
        raise NginxBootstrapError("Unexpected Nginx configuration or certificate mounts")

    if any(str(entry).startswith(FORBIDDEN_ENV) for entry in cfg.get("Env") or []):
        raise NginxBootstrapError("Unexpected secrets in Nginx environment")
    return item


def running(item):
    state = item.get("State") or {}
    return state.get("Running") is True and state.get("Status") == "running"


def check_local_tls():
    # Existing Phase 1 checks SAN, key matching, expiry and OS trust. Their
    # output is withheld; nothing is generated or installed in this phase.
    run([sys.executable, "-m", "scripts.dev.certificates", "check"], timeout=45)
    run([sys.executable, "-m", "scripts.dev.ca_trust", "check"], timeout=45)


def curl_bytes(url, *, port, path, timeout=12):
    # -q MUST be curl's first argument: ignore operator ~/.curlrc entirely.
    # Never use -k/--insecure: require the local mkcert CA to be trusted.
    raw = run([
        "curl", "-q", "--silent", "--show-error", "--noproxy", "*",
        "--connect-timeout", "3", "--max-time", str(timeout),
        "--resolve", f"{DOMAIN}:{port}:127.0.0.1",
        "--write-out", "\n%{http_code}", url + path,
    ], timeout=timeout + 4)
    try:
        body, status = raw.rsplit(b"\n", 1)
    except ValueError:
        raise NginxBootstrapError("Nginx HTTPS response status missing") from None
    if status != b"200":
        raise NginxBootstrapError("Nginx HTTPS request did not return HTTP 200")
    return body


def verify_routes():
    # Syntax check validates the actual configuration inside the container.
    run(["docker", "exec", NAME, "nginx", "-t"], timeout=20)

    base = f"https://{DOMAIN}:8443"
    page = curl_bytes(base, port=8443, path="/")
    if b'id="root"' not in page or b"/src/main.tsx" not in page:
        raise NginxBootstrapError("Nginx HTTPS frontend response invalid")

    module = curl_bytes(base, port=8443, path="/src/main.tsx")
    if b"createRoot" not in module:
        raise NginxBootstrapError("Nginx HTTPS Vite module unavailable")

    health = curl_bytes(base, port=8443, path="/api/v1/health")
    try:
        body = json.loads(health)
    except (ValueError, UnicodeError, TypeError):
        raise NginxBootstrapError("Nginx HTTPS API response invalid") from None
    if (not isinstance(body, dict)
            or (body.get("data") or {}).get("status") != "ok"
            or (body.get("data") or {}).get("database") != "ok"):
        raise NginxBootstrapError("Nginx HTTPS API or PostgreSQL health failed")

    # Confirm the exact HTTP->HTTPS redirect without following it, and without
    # displaying server response headers or cookies.
    redirect = run([
        "curl", "-q", "--silent", "--show-error", "--noproxy", "*",
        "--connect-timeout", "3", "--max-time", "8",
        "--resolve", f"{DOMAIN}:8080:127.0.0.1",
        "--output", "/dev/null", "--write-out", "%{http_code}\n%{redirect_url}",
        f"http://{DOMAIN}:8080/",
    ], timeout=12)
    if redirect.decode("utf-8", "replace") != f"308\n{base}/":
        raise NginxBootstrapError("Nginx HTTP-to-HTTPS redirect incorrect")

    print("PASS: Nginx TLS verified, Vite HTML/module and NestJS API via HTTPS", flush=True)
    print("PASS: Loopback HTTP 8080 redirects to HTTPS 8443", flush=True)


def wait_nginx(attempts=45, delay=2):
    for attempt in range(attempts):
        item = container()
        if item is None:
            raise NginxBootstrapError("Nginx container disappeared")
        verify_identity(item)
        state = item.get("State") or {}
        if running(item):
            try:
                verify_routes()
                return
            except NginxBootstrapError:
                pass
        if state.get("Status") in ("exited", "dead"):
            raise NginxBootstrapError("Nginx stopped; inspect local Docker logs")
        if attempt + 1 < attempts:
            time.sleep(delay)
    raise NginxBootstrapError("Nginx HTTPS readiness timeout; no cleanup attempted")


def require_prerequisites():
    if frontend.main(["check"]) != 0:
        raise NginxBootstrapError("Frontend or Vault-backed services not ready")
    check_local_tls()


def start_or_keep(env):
    item = container()
    if item is not None:
        verify_identity(item)
        status = (item.get("State") or {}).get("Status")
        if status == "running":
            print(f"KEEP: {NAME}", flush=True)
        elif status in ("created", "exited"):
            run(["docker", "start", NAME], timeout=90)
            print(f"START: {NAME}", flush=True)
        else:
            raise NginxBootstrapError("Unexpected existing Nginx state")
        return

    run(agent.app_compose(
        "up", "-d", "--no-deps", "--no-recreate", SERVICE,
    ), env=env, timeout=240)
    item = container()
    if item is None:
        raise NginxBootstrapError("Compose did not create Nginx")
    verify_identity(item)
    print(f"CREATED: {NAME}", flush=True)


def apply():
    with pg.LOCK.open("rb") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise NginxBootstrapError("Another startup or migration workflow is running") from None
        require_prerequisites()
        existing = container()
        if existing is not None:
            verify_identity(existing)
        start_or_keep(agent.safe_env())
        wait_nginx()
    print("RESULT: NGINX HTTPS READY (apply)", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "check", "apply"))
    args = parser.parse_args(argv)
    try:
        if args.mode == "plan":
            item = container()
            if item is None:
                print("TODO: Nginx HTTPS gateway not created (read-only)")
            else:
                verify_identity(item)
                print(f"EXISTS: Nginx HTTPS gateway ({(item.get('State') or {}).get('Status')})")
            print("RESULT: NGINX HTTPS PLAN COMPLETE (read-only)")
            return 0
        if args.mode == "check":
            require_prerequisites()
            item = container()
            if item is None:
                raise NginxBootstrapError("Nginx HTTPS gateway missing")
            verify_identity(item)
            if not running(item):
                raise NginxBootstrapError("Nginx HTTPS gateway not running")
            verify_routes()
            print("RESULT: NGINX HTTPS READY (read-only)", flush=True)
            return 0
        apply()
        return 0
    except (NginxBootstrapError, frontend.FrontendBootstrapError,
            support.SupportServicesError, pg.PostgresBootstrapError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("RESULT: NGINX HTTPS STOPPED (no resources removed)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
