"""Shared Docker Compose configuration for PostgreSQL tooling."""

import os
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]


def compose_command() -> list[str]:
    command = [sys.executable, str(ROOT_DIR / "scripts/dev/configuration.py"), "compose"]

    project = os.environ.get("FT_APP_COMPOSE_PROJECT")

    if project:
        command.extend(["-p", project])

    command.extend([
        "-f", str(ROOT_DIR / "compose.yml"),
    ])

    override = os.environ.get("FT_APP_COMPOSE_OVERRIDE")

    if override:
        path = Path(override).resolve()

        if not path.is_file():
            raise RuntimeError(
                "Compose override file does not exist"
            )

        command.extend(["-f", str(path)])

    return command
