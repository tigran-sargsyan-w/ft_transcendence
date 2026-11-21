#!/usr/bin/env python3
"""Verify Grafana through the trusted HTTPS gateway.

Read-only verification. Never starts or recreates containers, changes
configuration, installs certificates or retrieves authentication secrets.
"""

import argparse
import json
import sys

import bootstrap_backend_agent as agent
import bootstrap_nginx as nginx

BASE = f"https://{nginx.DOMAIN}:8443"
RUNTIME_CONFIG = "/etc/nginx/conf.d/default.conf"
EXPECTED_VERSION = "13.3.1"


class GrafanaHTTPSBootstrapError(Exception):
    """Public errors exclude HTTP response data and credentials."""


def require(condition, message):
    if not condition:
        raise GrafanaHTTPSBootstrapError(message)


def verify_gateway():
    item = nginx.container()

    require(item is not None, "Nginx gateway missing.")

    nginx.verify_identity(item)

    require(
        nginx.running(item),
        "Nginx gateway is not running.",
    )

    configuration = item.get("Config") or {}

    require(
        "MODSEC_RULE_ENGINE=On" in configuration.get("Env", []),
        "Nginx WAF blocking mode not enabled.",
    )

    runtime = nginx.run(
        ["docker", "exec", nginx.NAME, "cat", RUNTIME_CONFIG],
        env=agent.safe_env(),
        timeout=20,
    )

    require(
        runtime == nginx.CONFIG.read_bytes(),
        "Nginx runtime configuration differs from template.",
    )

    require(
        b"ctl:auditLogParts=-BCDEFGHIJK" in runtime
        and b"error_log /var/log/nginx/error.log crit;" in runtime,
        "Required Grafana logging protection missing.",
    )

    print("PASS: Grafana HTTPS gateway identity and policy.", flush=True)


def request(path, *, query=None):
    command = [
        "curl", "-q",
        "--silent", "--show-error",
        "--noproxy", "*",
        "--connect-timeout", "3",
        "--max-time", "12",
        "--resolve", f"{nginx.DOMAIN}:8443:127.0.0.1",
        "--write-out", "\n%{http_code}\n%{redirect_url}",
    ]

    if query is not None:
        command.extend(["--url-query", query])

    command.append(BASE + path)

    raw = nginx.run(
        command,
        env=agent.safe_env(),
        timeout=16,
    )

    try:
        body, status, redirect = raw.rsplit(b"\n", 2)

        require(
            len(status) == 3 and status.isdigit(),
            "Invalid Grafana HTTPS status.",
        )

        return int(status), body, redirect.decode("utf-8")

    except (ValueError, UnicodeError):
        raise GrafanaHTTPSBootstrapError(
            "Invalid Grafana HTTPS response."
        ) from None


def verify_routes():
    status, _, redirect = request("/grafana")

    require(
        status == 308 and redirect == BASE + "/grafana/",
        "Grafana canonical redirect mismatch.",
    )

    status, body, _ = request("/grafana/login")

    require(
        status == 200
        and len(body) > 10000
        and b"grafana" in body.lower()
        and b"</html>" in body.lower(),
        "Grafana login HTML unavailable or incomplete.",
    )

    print("PASS: Grafana HTTPS login and canonical redirect.", flush=True)

    status, body, _ = request("/grafana/api/health")

    require(status == 200, "Grafana HTTPS health unavailable.")

    try:
        health = json.loads(body)
    except (ValueError, UnicodeError):
        raise GrafanaHTTPSBootstrapError(
            "Invalid Grafana HTTPS health response."
        ) from None

    require(
        isinstance(health, dict)
        and health.get("database") == "ok"
        and health.get("version") == EXPECTED_VERSION,
        "Grafana database or version verification failed.",
    )

    print("PASS: Grafana HTTPS health and database.", flush=True)

    status, _, _ = request("/grafana/api/user")

    require(
        status == 401,
        "Grafana anonymous API access was not rejected.",
    )

    print("PASS: Grafana HTTPS anonymous access rejected.", flush=True)

    for path in ("/grafana/login", "/grafana/api/health"):
        status, _, _ = request(
            path,
            query="q=1' OR '1'='1",
        )

        require(
            status == 403,
            "Grafana inbound WAF blocking not verified.",
        )

    print("PASS: Grafana HTTPS WAF blocking.", flush=True)


def check():
    verify_gateway()
    verify_routes()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "check"))
    args = parser.parse_args(argv)

    try:
        if args.mode == "plan":
            print("PLAN: Verify existing Nginx gateway and WAF policy.")
            print("PLAN: Verify Grafana HTTPS routing and health.")
            print("PLAN: Verify anonymous access rejection and WAF blocking.")
            print("RESULT: GRAFANA HTTPS PLAN COMPLETE (read-only)")
            return 0

        check()

        print("RESULT: GRAFANA HTTPS READY (read-only)", flush=True)
        return 0

    except (GrafanaHTTPSBootstrapError,
            nginx.NginxBootstrapError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "RESULT: GRAFANA HTTPS CHECK FAILED",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
