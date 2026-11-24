#!/usr/bin/env python3
"""First-install-only Docker image preparation; no destructive updates."""

import argparse
import os
import subprocess
import sys

IMAGES = (
    "hashicorp/vault:2.2.1",
    "postgres:18-bookworm",
    "node:22-bookworm-slim",
    "python:3.12-slim-bookworm",
    "python:3.12-alpine",
    "tecnativa/docker-socket-proxy:latest",
    "owasp/modsecurity-crs:4.30.0-nginx-202611090911",
    "prom/prometheus:v3.15.0",
    "grafana/grafana:13.3.1",
)


class ImageError(Exception):
    pass


def run(*args, timeout=30):
    try:
        return subprocess.run(
            ["docker", *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ImageError(
            "Docker command failed or timed out"
        ) from None


def available(image):
    result = run(
        "image", "inspect",
        "--format", "{{.Id}}",
        image,
    )

    if result.returncode != 0:
        return False

    if not result.stdout.strip().startswith(b"sha256:"):
        raise ImageError("Unexpected local image identity")

    return True


def apply_missing(missing):
    if not missing:
        print("KEEP: All required Docker images already available.")
        return

    if not sys.stdin.isatty():
        raise ImageError(
            "Interactive confirmation required for image pulls"
        )

    for image in missing:
        print("MISSING:", image)

    answer = input(
        "Download these missing Docker images? [y/N] "
    ).strip().lower()

    if answer not in ("y", "yes"):
        raise ImageError("Image download declined")

    for image in missing:
        print("PULL:", image, flush=True)

        result = run(
            "pull", "--quiet", image, timeout=900,
        )

        if result.returncode != 0 or not available(image):
            raise ImageError(
                "Could not prepare required image: " + image
            )

        print("PASS:", image, flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=("plan", "check", "apply"),
    )
    args = parser.parse_args(argv)

    try:
        if os.geteuid() == 0:
            raise ImageError(
                "Run as an ordinary user, not sudo"
            )

        daemon = run(
            "info", "--format", "{{.ServerVersion}}",
            timeout=20,
        )

        if daemon.returncode != 0 or not daemon.stdout.strip():
            raise ImageError(
                "Docker daemon inaccessible to current user"
            )

        missing = [
            image for image in IMAGES
            if not available(image)
        ]

        if args.mode == "plan":
            print(
                f"Required images: {len(IMAGES)}; "
                f"missing: {len(missing)}"
            )

            for image in missing:
                print("TODO:", image)

            print("RESULT: IMAGE PLAN COMPLETE (read-only)")
            return 0

        if args.mode == "check" and missing:
            raise ImageError(
                "Required images missing: " + ", ".join(missing)
            )

        if args.mode == "apply":
            apply_missing(missing)

            if any(not available(image) for image in IMAGES):
                raise ImageError("Image preparation incomplete")

        print("RESULT: REQUIRED DOCKER IMAGES READY")
        return 0

    except ImageError as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
