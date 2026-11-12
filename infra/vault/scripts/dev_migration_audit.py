"""Read-only Prisma migration audit for development startup."""

import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "backend/prisma/migrations"
POSTGRES = "transcendence-app-dev-postgres-1"


class MigrationAuditError(Exception):
    pass


def audit_migrations(directory=MIGRATIONS):
    if not directory.is_dir() or directory.is_symlink():
        raise MigrationAuditError("Unsafe or missing migrations directory")

    local = {}

    for path in sorted(directory.iterdir()):
        if not path.is_dir():
            continue

        if path.is_symlink():
            raise MigrationAuditError("Migration directory is a symlink")

        if not re.fullmatch(r"[A-Za-z0-9_-]+", path.name):
            raise MigrationAuditError("Unexpected migration directory name")

        sql = path / "migration.sql"

        if not sql.is_file() or sql.is_symlink():
            raise MigrationAuditError(
                f"Missing or unsafe migration.sql: {path.name}"
            )

        local[path.name] = hashlib.sha256(
            sql.read_bytes()
        ).hexdigest()

    if not local:
        raise MigrationAuditError("No local migrations found")

    query = """
        SELECT migration_name,
               checksum,
               finished_at IS NOT NULL,
               rolled_back_at IS NOT NULL
        FROM public._prisma_migrations
        ORDER BY started_at, id
    """

    try:
        result = subprocess.run(
            [
                "docker", "exec", POSTGRES,
                "sh", "-c",
                'exec psql -X -A -t -F "|" -v ON_ERROR_STOP=1 '
                '-U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"',
                "_", query,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MigrationAuditError(
            "PostgreSQL migration audit unavailable"
        ) from exc

    if result.returncode != 0:
        raise MigrationAuditError(
            "PostgreSQL migration history unavailable"
        )

    applied = set()

    for line in result.stdout.splitlines():
        parts = line.split("|")

        if len(parts) != 4:
            raise MigrationAuditError(
                "Unexpected Prisma migration history"
            )

        name, checksum, finished, rolled = parts

        if finished not in ("t", "f") or rolled not in ("t", "f"):
            raise MigrationAuditError("Invalid migration state")

        if rolled == "t" and finished == "f":
            continue

        if finished != "t" or rolled != "f":
            raise MigrationAuditError(
                f"Failed or incomplete migration: {name}"
            )

        if name not in local:
            raise MigrationAuditError(
                f"Applied migration missing locally: {name}"
            )

        if name in applied:
            raise MigrationAuditError(
                f"Duplicate successful migration: {name}"
            )

        if checksum != local[name]:
            raise MigrationAuditError(
                f"Migration checksum mismatch: {name}"
            )

        applied.add(name)

    return sorted(set(local) - applied)
