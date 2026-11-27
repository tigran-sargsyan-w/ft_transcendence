#!/usr/bin/env python3
"""Phase 5E: first-start and verify the trusted Nginx HTTPS gateway.

Requires Phase 5D frontend and all previously verified services. Never
recreates existing containers; does not modify TLS certificates, credentials,
volumes, Vault or PostgreSQL. Publishes only configured loopback ports.
"""

if __package__:
    from . import bootstrap_plan_events as plan_events
else:
    import bootstrap_plan_events as plan_events

import argparse
import fcntl
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import bootstrap_backend_agent as agent
import bootstrap_frontend as frontend
import bootstrap_postgres as pg
import bootstrap_support_services as support
import nginx_tls_access as tls_access

# Direct script entry points (including bootstrap.sh) do not set PYTHONPATH.
# Resolve the package from the repository, just as other runtime helpers do.
if str(pg.ROOT) not in sys.path:
    sys.path.insert(0, str(pg.ROOT))
from scripts.dev import certificates

NAME = f"{pg.PROJECT}-nginx-1"
SERVICE = "nginx"
IMAGE = "owasp/modsecurity-crs:4.30.0-nginx-202611090911"
DEFAULT_NETWORK = f"{pg.PROJECT}_default"
PUBLIC = pg.configuration.load()
DOMAIN = PUBLIC.domain
HTTP_PORT = PUBLIC.http_port
HTTPS_PORT = PUBLIC.https_port
CERTS = pg.ROOT / "infra/nginx/certs"
CONFIG = pg.ROOT / "infra/nginx/default.conf.template"
PROMQL_RULES = pg.ROOT / "infra/nginx/waf/REQUEST-900-FT-GRAFANA-PROMQL.conf"
PROMQL_RULES_RUNTIME = "/opt/owasp-crs/rules/REQUEST-900-FT-GRAFANA-PROMQL.conf"
TLS_GROUP_GID = "43043"
PORTS = {
    "8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(HTTP_PORT)}],
    "8443/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(HTTPS_PORT)}],
}
FORBIDDEN_ENV = (
    "DATABASE_URL=", "DATABASE_URL_FILE=", "VAULT_TOKEN=",
    "POSTGRES_PASSWORD=", "PGPASSWORD=", "VAULT_SECRET_ID=",
)


class NginxBootstrapError(Exception):
    """Public errors exclude Docker command output and HTTP response bodies."""


# Private recovery-probe exit codes. Only these fixed routes returning HTTP
# 502 after verified TLS are eligible for additional recovery waiting.
RECOVERY_502 = {"/": 20, "/src/main.tsx": 21, "/api/v1/health": 22}


class NginxReadinessError(NginxBootstrapError):
    """Observed HTTP 502 on a reviewed HTTPS route, not proof of a DNS race."""

    def __init__(self, path):
        if path not in RECOVERY_502:
            raise ValueError("Unexpected recovery route")
        self.path = path
        super().__init__(f"Nginx HTTPS upstream returned HTTP 502 on {path}")


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
            or host.get("GroupAdd") != [TLS_GROUP_GID]
            or (host.get("RestartPolicy") or {}).get("Name") != "unless-stopped"):
        raise NginxBootstrapError("Unexpected Nginx network, ports or privileges")

    mounts = item.get("Mounts") or []
    if (len(mounts) != 3 or not support.backend.required_mount(
            item, "/etc/nginx/templates/conf.d/default.conf.template", kind="bind",
            source=CONFIG, readonly=True)
            or not support.backend.required_mount(
            item, "/etc/nginx/certs", kind="bind",
            source=CERTS, readonly=True)
            or not support.backend.required_mount(
            item, PROMQL_RULES_RUNTIME, kind="bind",
            source=PROMQL_RULES, readonly=True)):
        raise NginxBootstrapError("Unexpected Nginx configuration or certificate mounts")

    if any(str(entry).startswith(FORBIDDEN_ENV) for entry in cfg.get("Env") or []):
        raise NginxBootstrapError("Unexpected secrets in Nginx environment")
    return item


def running(item):
    state = item.get("State") or {}
    return state.get("Running") is True and state.get("Status") == "running"


def check_local_tls():
    # Check the local certificate chain, SAN, key matching and expiry.
    # Browser/OS trust is separate; no trust-store mutation in this phase.
    run([sys.executable, "-m", "scripts.dev.certificates", "check"], timeout=45)


def app_ca_args():
    try:
        return certificates.curl_ca_args()
    except certificates.LocalCAError as exc:
        raise NginxBootstrapError(str(exc)) from None


def curl_bytes(url, *, port, path, timeout=12):
    # -q MUST be curl's first argument: ignore operator ~/.curlrc entirely.
    # Verify HTTPS using the explicit public application CA, without OS trust.
    raw = run([
        "curl", "-q", "--silent", "--show-error", "--noproxy", "*",
        *app_ca_args(),
        "--connect-timeout", "3", "--max-time", str(timeout),
        "--resolve", f"{DOMAIN}:{port}:127.0.0.1",
        "--write-out", "\n%{http_code}", url + path,
    ], timeout=timeout + 4)
    try:
        body, status = raw.rsplit(b"\n", 1)
    except ValueError:
        raise NginxBootstrapError("Nginx HTTPS response status missing") from None
    if status == b"502" and path in RECOVERY_502:
        raise NginxReadinessError(path)
    if status != b"200":
        raise NginxBootstrapError("Nginx HTTPS request did not return HTTP 200")
    return body


def rendered_config():
    """Expected entrypoint envsubst result; retain native Nginx variables."""
    return (CONFIG.read_bytes()
            .replace(b"${DEV_DOMAIN}", DOMAIN.encode("ascii"))
            .replace(b"${DEV_HTTPS_PORT}", str(HTTPS_PORT).encode("ascii")))


def verify_routes():
    # Syntax check validates the actual configuration inside the container.
    run(["docker", "exec", NAME, "nginx", "-t"], timeout=20)

    base = PUBLIC.app_origin
    page = curl_bytes(base, port=HTTPS_PORT, path="/")
    if b'id="root"' not in page or b"/src/main.tsx" not in page:
        raise NginxBootstrapError("Nginx HTTPS frontend response invalid")

    module = curl_bytes(base, port=HTTPS_PORT, path="/src/main.tsx")
    if b"createRoot" not in module:
        raise NginxBootstrapError("Nginx HTTPS Vite module unavailable")

    health = curl_bytes(base, port=HTTPS_PORT, path="/api/v1/health")
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
        "--resolve", f"{DOMAIN}:{HTTP_PORT}:127.0.0.1",
        "--output", "/dev/null", "--write-out", "%{http_code}\n%{redirect_url}",
        PUBLIC.http_origin + "/",
    ], timeout=12)
    if redirect.decode("utf-8", "replace") != f"308\n{base}/":
        raise NginxBootstrapError("Nginx HTTP-to-HTTPS redirect incorrect")

    print("PASS: Nginx TLS verified, Vite HTML/module and NestJS API via HTTPS", flush=True)
    print("PASS: Loopback HTTP redirects to HTTPS", flush=True)


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
    if tls_access.main(["check"]) != 0:
        raise NginxBootstrapError("Dedicated Nginx TLS ACL not ready")


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


def verify_recovery_target(expected_id):
    """Pin the already-running gateway before and after each full probe."""
    item = container()
    if item is None or item.get("Id") != expected_id:
        raise NginxBootstrapError("Nginx identity changed during recovery")
    verify_identity(item)
    if not running(item):
        raise NginxBootstrapError("Nginx stopped during recovery")


def recovery_probe(expected_id):
    """Same full readiness checks, with a distinct outcome for route HTTP 502."""
    verify_recovery_target(expected_id)
    require_prerequisites()
    verify_recovery_target(expected_id)
    try:
        verify_routes()
    except NginxReadinessError:
        # Never return a retryable result if the inspected target changed.
        verify_recovery_target(expected_id)
        raise
    verify_recovery_target(expected_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "check", "apply", "check-recovery"))
    parser.add_argument("--expected-id", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.mode == "check-recovery":
        if not args.expected_id or not re.fullmatch(r"[0-9a-f]{64}", args.expected_id):
            parser.error("Recovery probe requires the full inspected Docker ID")
    elif args.expected_id is not None:
        parser.error("Expected ID is only valid for the recovery probe")
    try:
        if args.mode == "check-recovery":
            try:
                recovery_probe(args.expected_id)
            except NginxReadinessError as exc:
                print(f"WAIT: {exc}", file=sys.stderr, flush=True)
                return RECOVERY_502[exc.path]
            print("RESULT: NGINX HTTPS READY (read-only)", flush=True)
            return 0
        if args.mode == "plan":
            item = container()
            if item is None:
                plan_events.container("Nginx HTTPS gateway", item)
                print("TODO: Nginx HTTPS gateway not created (read-only)")
            else:
                verify_identity(item)
                plan_events.container("Nginx HTTPS gateway", item)
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
