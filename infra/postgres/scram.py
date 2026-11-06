"""Generate PostgreSQL SCRAM-SHA-256 password verifiers."""

import base64
import hashlib
import hmac
import secrets


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def make_scram_verifier(password: str) -> str:
    """Create a PostgreSQL-compatible SCRAM-SHA-256 verifier."""

    if not password:
        raise ValueError("Password must not be empty")

    # ASCII avoids SASLprep compatibility differences.
    # Non-ASCII passwords require proper SASLprep handling.
    if not password.isascii() or any(
        ord(char) < 32 or ord(char) == 127
        for char in password
    ):
        raise ValueError(
            "Provisioning currently requires a printable ASCII password"
        )

    iterations = 4096
    salt = secrets.token_bytes(16)

    salted_password = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )

    client_key = hmac.new(
        salted_password,
        b"Client Key",
        hashlib.sha256,
    ).digest()

    stored_key = hashlib.sha256(client_key).digest()

    server_key = hmac.new(
        salted_password,
        b"Server Key",
        hashlib.sha256,
    ).digest()

    return (
        f"SCRAM-SHA-256${iterations}:{_b64(salt)}"
        f"${_b64(stored_key)}:{_b64(server_key)}"
    )
