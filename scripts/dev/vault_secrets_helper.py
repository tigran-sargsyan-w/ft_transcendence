#!/usr/bin/env python3
"""Ephemeral Vault-private helper: create-once PostgreSQL DSNs without output.

The Vault root token comes from stdin. Strong independent passwords are
created *inside* this isolated helper and are only sent to Vault over TLS.
No secret, token, credential URL, or exception body is printed.
"""

import json
import secrets
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from credential_contract import parse_database_url

ENDPOINT = "https://vault:8200/v1/kv/data/ft-transcendence/dev/"
CA = "/vault/tls/ca.crt"
MARKER = "FT_VAULT_DATABASE_SECRETS_RESULT="
ROLES = {"backend": "transcendence_app", "migrator": "transcendence_migrator"}


class SecretsError(Exception):
    """Error messages contain no secret-bearing data."""


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # Never forward X-Vault-Token to an endpoint selected by a redirect.
        return None


class VaultClient:
    def __init__(self, token):
        context = ssl.create_default_context(cafile=CA)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        # Never forward an operator token through an HTTP(S) proxy.
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), RejectRedirects())
        self.token = token

    def request(self, purpose, *, payload=None):
        if purpose not in ROLES:
            raise SecretsError("Unexpected database credential purpose")
        method = "POST" if payload is not None else "GET"
        headers = {"X-Vault-Token": self.token, "Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = Request(ENDPOINT + purpose, data=body, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=12) as response:
                raw = response.read(1024 * 1024 + 1)
        except HTTPError as exc:
            if method == "GET" and exc.code == 404:
                return None
            raise SecretsError(f"Vault {method} {purpose} rejected (HTTP {exc.code})") from None
        except (URLError, OSError, TimeoutError, ssl.SSLError):
            raise SecretsError(f"Vault {method} {purpose} unavailable or TLS failed") from None
        if len(raw) > 1024 * 1024:
            raise SecretsError(f"Vault {purpose} response too large")
        try:
            result = json.loads(raw)
        except (UnicodeError, ValueError):
            raise SecretsError(f"Malformed Vault {purpose} response") from None
        if not isinstance(result, dict):
            raise SecretsError(f"Unexpected Vault {purpose} response type")
        return result


def validate_stored(purpose, response):
    try:
        secret = response["data"]["data"]
        metadata = response["data"]["metadata"]
        if (not isinstance(secret, dict) or set(secret) != {"database_url"}
                or not isinstance(metadata, dict)
                or not isinstance(metadata.get("version"), int)
                or metadata["version"] < 1):
            raise ValueError("Unexpected KV v2 shape")
        # Match the same database URL contract used by PostgreSQL provisioning.
        parse_database_url(secret["database_url"], ROLES[purpose])
        return secret["database_url"]
    except (KeyError, TypeError, ValueError):
        raise SecretsError(f"Existing {purpose} Vault secret violates database URL contract") from None


def reconcile(client, mode):
    if mode not in ("check", "apply"):
        raise SecretsError("Unexpected helper mode")
    states = {}
    for purpose, username in ROLES.items():
        found = client.request(purpose)
        if found is not None:
            validate_stored(purpose, found)
            states[purpose] = "preserved"
            continue
        if mode == "check":
            states[purpose] = "missing"
            continue
        # Separate random credentials. Neither is saved to disk or printed.
        password = secrets.token_urlsafe(48)
        dsn = f"postgresql://{username}:{password}@postgres:5432/transcendence?schema=public"
        client.request(purpose, payload={
            "options": {"cas": 0},
            "data": {"database_url": dsn},
        })
        # Read the new value back to verify the contract, without logging it.
        written = client.request(purpose)
        if written is None:
            raise SecretsError(f"New {purpose} secret not found after creation")
        if validate_stored(purpose, written) != dsn:
            raise SecretsError(f"New {purpose} secret verification mismatch")
        states[purpose] = "created"
    return states


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in ("check", "apply"):
        print("ERROR: Unsupported helper mode", file=sys.stderr)
        return 2
    token = sys.stdin.buffer.readline(4097)
    if len(token) > 4096 or not token.endswith(b"\n"):
        print("ERROR: Invalid operator input", file=sys.stderr)
        return 1
    try:
        value = token[:-1].decode("utf-8")
        if not value or any(ch in value for ch in "\r\n\x00"):
            raise SecretsError("Invalid operator token")
        states = reconcile(VaultClient(value), argv[0])
    except (SecretsError, OSError, ssl.SSLError, UnicodeError) as exc:
        # Exceptions are tightly controlled and never include Vault responses.
        if isinstance(exc, SecretsError):
            print(f"ERROR: {exc}", file=sys.stderr)
        else:
            print("ERROR: Vault secret helper initialization failed", file=sys.stderr)
        return 1
    print(MARKER + json.dumps(states, separators=(",", ":")), flush=True)
    return 2 if "missing" in states.values() else 0


if __name__ == "__main__":
    raise SystemExit(main())
