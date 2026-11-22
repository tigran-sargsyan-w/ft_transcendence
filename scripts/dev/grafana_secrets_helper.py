#!/usr/bin/env python3
"""Create-once Grafana credentials inside an isolated Vault helper."""

import json
import re
import secrets
import ssl
import sys

from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

ENDPOINT = "https://vault:8200/v1/kv/data/ft-transcendence/dev/grafana"
CA = "/vault/tls/ca.crt"
MARKER = "FT_GRAFANA_SECRETS_RESULT="
FIELDS = {"admin_password", "secret_key"}


class SecretsError(Exception):
    """Safe diagnostics without credential values."""


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class VaultClient:
    def __init__(self, token):
        context = ssl.create_default_context(cafile=CA)
        context.minimum_version = ssl.TLSVersion.TLSv1_2

        self.opener = build_opener(
            ProxyHandler({}),
            HTTPSHandler(context=context),
            RejectRedirects(),
        )
        self.token = token

    def request(self, *, payload=None):
        method = "POST" if payload is not None else "GET"

        headers = {
            "X-Vault-Token": self.token,
            "Accept": "application/json",
        }

        body = None

        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(
            ENDPOINT,
            data=body,
            headers=headers,
            method=method,
        )

        try:
            with self.opener.open(request, timeout=12) as response:
                raw = response.read(1024 * 1024 + 1)
        except HTTPError as exc:
            if method == "GET" and exc.code == 404:
                return None

            raise SecretsError(
                f"Vault Grafana {method} rejected (HTTP {exc.code})"
            ) from None
        except (URLError, OSError, TimeoutError, ssl.SSLError):
            raise SecretsError(
                "Vault connection unavailable or TLS verification failed"
            ) from None

        if len(raw) > 1024 * 1024:
            raise SecretsError("Unexpected Vault response size")

        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise SecretsError("Malformed Vault response") from None

        if not isinstance(result, dict):
            raise SecretsError("Unexpected Vault response type")

        return result


def validate_stored(response):
    try:
        data = response["data"]["data"]
        metadata = response["data"]["metadata"]

        if not isinstance(data, dict) or set(data) != FIELDS:
            raise ValueError

        version = metadata["version"]

        if type(version) is not int or version < 1:
            raise ValueError

        for value in data.values():
            if not isinstance(value, str):
                raise ValueError

            if re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError

        if data["admin_password"] == data["secret_key"]:
            raise ValueError

        return data

    except (KeyError, TypeError, ValueError):
        raise SecretsError(
            "Existing Grafana secret violates the credential contract"
        ) from None


def reconcile(client, mode):
    if mode not in ("check", "apply"):
        raise SecretsError("Unexpected provisioning mode")

    existing = client.request()

    if existing is not None:
        validate_stored(existing)
        return "preserved"

    if mode == "check":
        return "missing"

    generated = {
        "admin_password": secrets.token_hex(32),
        "secret_key": secrets.token_hex(32),
    }

    while generated["secret_key"] == generated["admin_password"]:
        generated["secret_key"] = secrets.token_hex(32)

    client.request(payload={
        "options": {"cas": 0},
        "data": generated,
    })

    written = client.request()

    if written is None or validate_stored(written) != generated:
        raise SecretsError("New Grafana secret verification failed")

    return "created"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    if len(argv) != 1 or argv[0] not in ("check", "apply"):
        print("ERROR: Unsupported Grafana helper mode", file=sys.stderr)
        return 1

    raw = sys.stdin.buffer.readline(4097)

    if len(raw) > 4096 or not raw.endswith(b"\n"):
        print("ERROR: Invalid operator input", file=sys.stderr)
        return 1

    try:
        token = raw[:-1].decode("utf-8")

        if not token or any(c in token for c in "\r\n\x00"):
            raise SecretsError("Invalid operator token")

        result = reconcile(VaultClient(token), argv[0])

    except SecretsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    except (OSError, UnicodeError, ssl.SSLError):
        print(
            "ERROR: Grafana secret helper initialization failed",
            file=sys.stderr,
        )
        return 1

    print(MARKER + result, flush=True)

    return 2 if result == "missing" else 0


if __name__ == "__main__":
    raise SystemExit(main())
