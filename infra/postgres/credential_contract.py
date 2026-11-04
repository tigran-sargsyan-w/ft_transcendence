"""Validation of PostgreSQL credentials delivered by Vault."""

import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class DatabaseCredential:
    username: str
    password: str = field(repr=False)
    hostname: str
    port: int
    database: str


def parse_database_url(
    database_url: str,
    expected_user: str,
) -> DatabaseCredential:
    """Validate a Vault PostgreSQL DSN without exposing its contents."""

    if not database_url or any(c in database_url for c in "\r\n\x00"):
        raise ValueError("Invalid PostgreSQL DSN")

    try:
        parsed = urlsplit(database_url)

        if parsed.scheme not in ("postgresql", "postgres"):
            raise ValueError("Unsupported PostgreSQL URL scheme")

        if parsed.fragment:
            raise ValueError("PostgreSQL URL fragments are not allowed")

        raw_user = parsed.username or ""
        raw_password = parsed.password or ""

        for value in (raw_user, raw_password, parsed.path):
            if re.search(r"%(?![0-9A-Fa-f]{2})", value):
                raise ValueError("Invalid URL encoding")

        username = unquote(raw_user, errors="strict")
        password = unquote(raw_password, errors="strict")
        database = unquote(parsed.path.lstrip("/"), errors="strict")
        hostname = parsed.hostname
        port = parsed.port or 5432

    except (UnicodeError, ValueError) as exc:
        raise ValueError("Invalid PostgreSQL DSN structure") from None

    if username != expected_user:
        raise ValueError("Unexpected PostgreSQL role")

    if not password:
        raise ValueError("PostgreSQL password is missing")

    if hostname != "postgres" or port != 5432:
        raise ValueError("Unexpected PostgreSQL host or port")

    if database != "transcendence":
        raise ValueError("Unexpected PostgreSQL database")

    return DatabaseCredential(
        username=username,
        password=password,
        hostname=hostname,
        port=port,
        database=database,
    )
