#!/usr/bin/env python3
"""Verify Grafana datasource requests and targeted WAF exclusions.

Isolated mode uses synthetic responses, no credentials, no published
ports and no application networks. Live mode verifies actual Grafana
metric samples through the trusted HTTPS gateway.
"""

import argparse
import copy
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote
import uuid

import bootstrap_backend_agent as agent
import bootstrap_grafana as grafana
import bootstrap_grafana_https as https
import bootstrap_nginx as nginx

ROOT = nginx.pg.ROOT
BASE = https.BASE
QUERY_PATH = "/grafana/api/ds/query?ds_type=prometheus"

ENV = agent.safe_env()

FIRING = 'count(ALERTS{alertstate="firing"}) or vector(0)'
ATTACK = "1' OR '1'='1"

LABEL = "ft.monitoring.datasource-test"

ISOLATED_CONFIG = """
server {
    listen 8080;
    server_name __FT_DEV_DOMAIN__;

    location ^~ /grafana/ {
        proxy_pass http://127.0.0.1:18081;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }
}

server {
    listen 127.0.0.1:18081;

    modsecurity off;

    location / {
        default_type application/json;
        return 200 '{"synthetic":true}';
    }
}
""".replace("__FT_DEV_DOMAIN__", nginx.DOMAIN)


class VerificationError(Exception):
    """Safe diagnostics without raw credentials or HTTP response bodies."""


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def execute(args, *, data=None, timeout=40, check=True):
    try:
        result = subprocess.run(
            args,
            cwd=ROOT,
            env=ENV,
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise VerificationError(
            f"Command unavailable or timed out: {args[0]}"
        ) from None

    if check:
        require(
            result.returncode == 0,
            f"Command failed: {args[0]} "
            f"(exit {result.returncode}; output withheld).",
        )

    return result


def docker(*args, **kwargs):
    return execute(["docker", *args], **kwargs)


def dashboard_payloads():
    end_ms = int(time.time() * 1000)
    panels = []

    directory = ROOT / "infra/monitoring/grafana/dashboards"

    for filename in (
        "demo-lab.json",
        "monitoring-pipeline.json",
    ):
        dashboard = json.loads(
            (directory / filename).read_text(encoding="utf-8")
        )

        for panel in dashboard["panels"]:
            payload = {
                "queries": [
                    {
                        **target,
                        "intervalMs": 15000,
                        "maxDataPoints": 800,
                    }
                    for target in panel["targets"]
                ],
                "from": str(end_ms - 1800000),
                "to": str(end_ms),
            }

            panels.append((
                dashboard["uid"],
                panel["title"],
                payload,
            ))

    require(len(panels) == 11, "Unexpected dashboard inventory.")

    return panels


def encode(payload):
    if isinstance(payload, str):
        return payload

    return json.dumps(
        payload,
        separators=(",", ":"),
    )


def parse_response(raw):
    try:
        response, status = raw.rsplit(b"\n", 1)
    except ValueError:
        raise VerificationError("Invalid HTTP response.") from None

    require(
        len(status) == 3 and status.isdigit(),
        "Invalid HTTP status.",
    )

    return int(status), response


def verify_samples(payload, response):
    try:
        results = json.loads(response).get("results")
    except (ValueError, UnicodeError, AttributeError):
        raise VerificationError(
            "Invalid datasource JSON response."
        ) from None

    references = {
        query["refId"]
        for query in payload["queries"]
    }

    require(
        isinstance(results, dict)
        and set(results) == references,
        "Unexpected datasource result inventory.",
    )

    frames_count = 0
    samples_count = 0

    for ref in references:
        result = results[ref]

        require(
            not result.get("error")
            and int(result.get("status", 200)) < 400,
            "Datasource query returned an error.",
        )

        frames = result.get("frames") or []
        samples = 0

        for frame in frames:
            columns = frame.get("data", {}).get("values") or []

            if len(columns) >= 2:
                samples += sum(
                    value is not None
                    for value in columns[1]
                )

        require(
            samples > 0,
            "Datasource query contains no actual metric samples.",
        )

        frames_count += len(frames)
        samples_count += samples

    return frames_count, samples_count


def approved_payload(panels):
    payload = copy.deepcopy(panels[0][2])
    payload["queries"][0]["expr"] = FIRING
    return payload


def security_controls(panels):
    approved = approved_payload(panels)

    malicious = copy.deepcopy(approved)
    malicious["queries"][0]["expr"] = ATTACK

    mixed = copy.deepcopy(approved)
    mixed["queries"][0]["legendFormat"] = ATTACK

    appended = copy.deepcopy(approved)
    appended["queries"][0]["expr"] += " " + ATTACK

    second = copy.deepcopy(approved)
    second["queries"].append(
        copy.deepcopy(second["queries"][0])
    )
    second["queries"][1]["refId"] = "B"
    second["queries"][1]["expr"] = ATTACK

    duplicate = encode(approved)
    field = '"expr":' + json.dumps(FIRING)

    require(
        duplicate.count(field) == 1,
        "Unexpected duplicate-expression fixture.",
    )

    duplicate = duplicate.replace(
        field,
        field + ',"expr":' + json.dumps(ATTACK),
        1,
    )

    return (
        (
            "SQL injection in URL",
            approved,
            QUERY_PATH + "&q=" + quote(ATTACK, safe=""),
        ),
        (
            "Unapproved malicious expression",
            malicious,
            QUERY_PATH,
        ),
        (
            "Malicious value in another JSON field",
            mixed,
            QUERY_PATH,
        ),
        (
            "Modified approved expression",
            appended,
            QUERY_PATH,
        ),
        (
            "Malicious second expression",
            second,
            QUERY_PATH,
        ),
        (
            "Exception outside query endpoint",
            approved,
            "/grafana/api/other",
        ),
        (
            "URL/JSON argument collision",
            approved,
            QUERY_PATH
            + "&json.queries.array_0.expr="
            + quote(ATTACK, safe=""),
        ),
        (
            "Ambiguous duplicate expression",
            duplicate,
            QUERY_PATH,
        ),
    )


def verify_requests(request, panels, *, live):
    for uid, title, payload in panels:
        status, response = request(payload, QUERY_PATH)

        require(
            status == 200,
            f"{uid} / {title}: HTTP {status}",
        )

        message = f"PASS: {uid} / {title}: HTTP 200"

        if live:
            frames, samples = verify_samples(payload, response)
            message += f", frames={frames}, samples={samples}"

        print(message, flush=True)

    print("PASS: All eleven dashboard queries accepted.")

    for label, payload, path in security_controls(panels):
        status, _ = request(payload, path)

        require(
            status == 403,
            f"WAF regression: {label} (HTTP {status})",
        )

        print(f"PASS: {label} (HTTP 403)", flush=True)

    print("PASS: All eight WAF security controls.")


def compose_policy():
    model = json.loads(execute(
        agent.app_compose("config", "--format", "json"),
    ).stdout)

    service = model["services"]["nginx"]

    require(
        service.get("image") == nginx.IMAGE
        and not service.get("build"),
        "Unexpected Nginx image contract.",
    )

    environment = service.get("environment") or {}

    require(
        environment.get("MODSEC_RULE_ENGINE") == "On",
        "WAF blocking mode must be enabled.",
    )

    mounts = [
        mount
        for mount in service.get("volumes", [])
        if mount.get("target") == nginx.PROMQL_RULES_RUNTIME
    ]

    require(
        len(mounts) == 1
        and mounts[0].get("type") == "bind"
        and mounts[0].get("read_only") is True
        and mounts[0].get("source") == str(nginx.PROMQL_RULES),
        "Unexpected PromQL CRS mount contract.",
    )

    allowed_keys = (
        "MODSEC_RULE_ENGINE",
        "BLOCKING_PARANOIA",
        "DETECTION_PARANOIA",
        "ANOMALY_INBOUND",
        "ANOMALY_OUTBOUND",
        "ALLOWED_METHODS",
        "ALLOWED_REQUEST_CONTENT_TYPE",
    )

    flags = []

    for key in allowed_keys:
        if key in environment:
            flags.extend([
                "--env",
                f"{key}={environment[key]}",
            ])

    return flags


def isolated():
    panels = dashboard_payloads()
    policy = compose_policy()

    require(
        nginx.PROMQL_RULES.is_file()
        and not nginx.PROMQL_RULES.is_symlink(),
        "Trusted CRS source unavailable.",
    )

    name = "ft-waf-datasource-test-" + uuid.uuid4().hex[:10]

    def request(payload, path):
        return parse_response(docker(
            "exec", name,
            "curl", "-q",
            "--silent", "--show-error",
            "--noproxy", "*",
            "--max-time", "10",
            "--header", f"Host: {nginx.DOMAIN}:{nginx.HTTPS_PORT}",
            "--header", f"Origin: {BASE}",
            "--header", "Content-Type: application/json",
            "--request", "POST",
            "--data-binary", encode(payload),
            "--write-out", "\n%{http_code}",
            "http://127.0.0.1:8080" + path,
            timeout=15,
        ).stdout)

    with tempfile.TemporaryDirectory(
        prefix="ft-waf-datasource-",
    ) as directory:
        template = Path(directory) / "default.conf.template"
        template.write_text(ISOLATED_CONFIG, encoding="utf-8")
        template.chmod(0o644)

        try:
            docker(
                "run", "--detach", "--rm",
                "--pull", "never",
                "--network", "none",
                "--no-healthcheck",
                "--name", name,
                "--label", f"{LABEL}={name}",
                "--env", "PORT=8080",
                *policy,
                "--mount",
                (
                    f"type=bind,src={template},"
                    "dst=/etc/nginx/templates/conf.d/"
                    "default.conf.template,readonly"
                ),
                "--mount",
                (
                    f"type=bind,src={nginx.PROMQL_RULES},"
                    f"dst={nginx.PROMQL_RULES_RUNTIME},readonly"
                ),
                nginx.IMAGE,
                timeout=90,
            )

            ready = False

            for _ in range(30):
                try:
                    status, _ = request(panels[0][2], QUERY_PATH)

                    if status == 200:
                        ready = True
                        break

                except VerificationError:
                    pass

                time.sleep(1)

            require(ready, "Isolated WAF startup failed.")

            docker("exec", name, "nginx", "-t")

            require(
                docker(
                    "exec", name, "cat",
                    nginx.PROMQL_RULES_RUNTIME,
                ).stdout == nginx.PROMQL_RULES.read_bytes(),
                "Mounted CRS policy differs from source.",
            )

            print("PASS: Isolated WAF configuration.")

            verify_requests(request, panels, live=False)

        finally:
            result = docker("inspect", name, check=False)

            if result.returncode == 0:
                item = json.loads(result.stdout)[0]

                require(
                    item["Config"]["Labels"].get(LABEL) == name,
                    "Unexpected test container ownership.",
                )

                docker("rm", "--force", item["Id"])

                print("PASS: Isolated test container removed.")

    print("RESULT: ISOLATED GRAFANA DATASOURCE WAF TEST PASSED")


def live():
    require(
        https.main(["check"]) == 0,
        "HTTPS prerequisite verification failed.",
    )

    grafana.verify_identity(grafana.container())
    grafana.probe_http()

    panels = dashboard_payloads()

    password = docker(
        "exec", grafana.NAME,
        "cat", "/run/secrets/grafana/admin_password",
    ).stdout.decode("ascii").strip()

    require(
        re.fullmatch(r"[0-9a-f]{64}", password) is not None,
        "Unexpected Grafana credential format.",
    )

    auth = f'user = "admin:{password}"\n'.encode("ascii")
    del password

    def request(payload, path, *, authenticated=True):
        return parse_response(execute(
            [
                "curl", "-q", "--config", "-",
                "--silent", "--show-error",
                "--noproxy", "*",
                *nginx.app_ca_args(),
                "--connect-timeout", "5",
                "--max-time", "20",
                "--resolve", f"{nginx.DOMAIN}:{nginx.HTTPS_PORT}:127.0.0.1",
                "--header", f"Origin: {BASE}",
                "--header", "Content-Type: application/json",
                "--request", "POST",
                "--data-binary", encode(payload),
                "--write-out", "\n%{http_code}",
                BASE + path,
            ],
            data=auth if authenticated else b"",
            timeout=25,
        ).stdout)

    try:
        verify_requests(request, panels, live=True)

        status, _ = request(
            approved_payload(panels),
            QUERY_PATH,
            authenticated=False,
        )

        require(
            status == 401,
            "Anonymous datasource access was not rejected.",
        )

        print("PASS: Anonymous datasource access rejected.")

    finally:
        del auth

    print("RESULT: LIVE GRAFANA DATASOURCE TEST PASSED")


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    modes = parser.add_mutually_exclusive_group(required=True)

    modes.add_argument("--isolated", action="store_true")
    modes.add_argument("--live", action="store_true")

    args = parser.parse_args()

    try:
        if args.isolated:
            isolated()
        else:
            live()

        return 0

    except Exception as exc:
        if isinstance(exc, VerificationError):
            print(f"ERROR: {exc}", file=sys.stderr)
        else:
            print(
                "ERROR: Datasource verification failed "
                f"({type(exc).__name__}; details withheld).",
                file=sys.stderr,
            )

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
