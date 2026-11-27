"""Read-only, strict loader for the public development configuration.

This file is not a shell script or a general-purpose dotenv loader. Only the
explicit schema is accepted; values never come from ambient environment variables.
"""

from dataclasses import dataclass
from pathlib import Path
import os
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "config" / "development.env"
KEYS = frozenset({
    "DEV_DOMAIN", "DEV_HTTP_PORT", "DEV_HTTPS_PORT", "DEV_VAULT_UI_PORT",
    "SESSION_TTL_HOURS", "COOKIE_SECURE",
})
MAX_SESSION_TTL_HOURS = (2**53 - 1) // 3_600_000
LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.ASCII)


class ConfigurationError(ValueError):
    """Public diagnostic; never includes unvalidated configuration values."""


def parse(text: str) -> dict[str, str]:
    values = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or key not in KEYS:
            raise ConfigurationError(f"Invalid configuration key or syntax on line {number}")
        if key in values:
            raise ConfigurationError(f"Duplicate configuration key: {key}")
        values[key] = value
    missing = KEYS - values.keys()
    if missing:
        raise ConfigurationError("Missing configuration keys: " + ", ".join(sorted(missing)))
    return values


def domain(value: str) -> str:
    labels = value.split(".")
    if value == "localhost":
        return value
    if (len(value) > 253 or len(labels) < 2 or labels[-1] != "test"
            or not all(LABEL.fullmatch(label) for label in labels)):
        raise ConfigurationError("DEV_DOMAIN must be localhost or a lowercase .test hostname")
    return value


def integer(value: str, key: str, minimum: int, maximum: int) -> int:
    if not re.fullmatch(r"[1-9][0-9]{0,9}", value):
        raise ConfigurationError(f"{key} must be a decimal integer in [{minimum}, {maximum}]")
    result = int(value)
    if not minimum <= result <= maximum:
        raise ConfigurationError(f"{key} must be in [{minimum}, {maximum}]")
    return result


@dataclass(frozen=True)
class DevelopmentConfiguration:
    domain: str
    http_port: int
    https_port: int
    vault_ui_port: int
    session_ttl_hours: int

    @property
    def app_origin(self) -> str:
        return f"https://{self.domain}:{self.https_port}"

    @property
    def http_origin(self) -> str:
        return f"http://{self.domain}:{self.http_port}"

    @property
    def grafana_root_url(self) -> str:
        return f"{self.app_origin}/grafana/"

    @property
    def health_url(self) -> str:
        return f"{self.app_origin}/api/v1/health"

    @property
    def vault_ui_url(self) -> str:
        # Vault TLS covers localhost independently of the configurable app domain.
        return f"https://localhost:{self.vault_ui_port}/ui/"

    def public_environment(self) -> dict[str, str]:
        """Explicit allowlist for future guarded Compose/template consumers."""
        return {
            "DEV_DOMAIN": self.domain,
            "DEV_HTTP_PORT": str(self.http_port),
            "DEV_HTTPS_PORT": str(self.https_port),
            "DEV_VAULT_UI_PORT": str(self.vault_ui_port),
            "SESSION_TTL_HOURS": str(self.session_ttl_hours),
            "COOKIE_SECURE": "true",
            "APP_ORIGIN": self.app_origin,
            "GRAFANA_ROOT_URL": self.grafana_root_url,
        }


def private_primary_group(uid: int, gid: int) -> bool:
    """Recognize a local user-private primary group (the Ubuntu umask 002 case)."""
    try:
        import grp
        import pwd
        owner = pwd.getpwuid(uid)
        group = grp.getgrgid(gid)
        return (owner.pw_gid == gid
                and not (set(group.gr_mem) - {owner.pw_name})
                and not any(user.pw_uid != uid and user.pw_gid == gid
                            for user in pwd.getpwall()))
    except (ImportError, KeyError, OSError):
        return False


def read_source(path: Path) -> tuple[str, os.stat_result]:
    """Read the public file without following symlinks or unsafe POSIX mode bits.

    Read-only consumers may read another user's checkout. A writer must also
    verify ownership. This is a file-mode check, not a full checkout/ACL audit.
    """
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ConfigurationError("Unsafe public configuration file type")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode)
                    or (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino)):
                raise ConfigurationError("Unsafe public configuration file type")
            if os.name == "posix" and (info.st_mode & 0o002 or (
                    info.st_mode & 0o020 and not private_primary_group(info.st_uid, info.st_gid))):
                raise ConfigurationError("Unsafe public configuration permissions")
            return stream.read(), info
    except (OSError, UnicodeError):
        raise ConfigurationError("Cannot read public development configuration") from None


def load(path: Path = CONFIG_FILE) -> DevelopmentConfiguration:
    text, _ = read_source(path)
    return from_text(text)


def from_text(text: str) -> DevelopmentConfiguration:
    values = parse(text)
    ports = [integer(values[key], key, 1024, 65535) for key in (
        "DEV_HTTP_PORT", "DEV_HTTPS_PORT", "DEV_VAULT_UI_PORT",
    )]
    if len(set(ports)) != len(ports):
        raise ConfigurationError("Development host ports must be distinct")
    if values["COOKIE_SECURE"] != "true":
        raise ConfigurationError("COOKIE_SECURE must be true for HTTPS development")
    # Keep the backend conversion to milliseconds within JS safe integers.
    return DevelopmentConfiguration(
        domain(values["DEV_DOMAIN"]), *ports,
        integer(values["SESSION_TTL_HOURS"], "SESSION_TTL_HOURS", 1, MAX_SESSION_TTL_HOURS),
    )


def compose_command(arguments: list[str]) -> None:
    """Run Compose with validated public values, without implicit .env loading.

    Non-public inputs remain the caller's responsibility. Guarded workflows
    continue to pass their existing allowlisted environment to this adapter.
    """
    env = os.environ.copy()
    env.update(load().public_environment())
    os.execvpe("docker", ["docker", "compose", "--env-file", "/dev/null", *arguments], env)


def main() -> int:
    try:
        if sys.argv[1:2] == ["compose"]:
            compose_command(sys.argv[2:])
            return 0
        if sys.argv[1:] == ["origin"]:
            print(load().app_origin)
            return 0
        if len(sys.argv) != 1:
            print("Usage: configuration.py [origin | compose ARGS...]", file=sys.stderr)
            return 2
        config = load()
    except ConfigurationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("ERROR: Docker Compose unavailable", file=sys.stderr)
        return 1
    print("[OK] Public development configuration validated")
    for key, value in config.public_environment().items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
