#!/usr/bin/env python3
"""Synthetic progress rendering comparison; no provisioning or real terminal."""

import argparse
import importlib.util
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
RENDERER = "scripts/dev/bootstrap_progress.py"
LABELS = ("System dependencies", "Docker images", "host", "resources", "vault",
          "vault-ui", "vault-config", "vault-secrets", "postgres", "postgres-roles",
          "migrations", "backend-agent", "backend", "support-services", "frontend",
          "nginx", "prometheus", "grafana-secrets", "grafana-agent", "grafana",
          "grafana-https")


class Sink:
    def __init__(self):
        self.writes = self.bytes = self.erases = 0

    def isatty(self):
        return True

    def write(self, value):
        self.writes += 1
        self.bytes += len(value.encode("utf-8"))
        self.erases += value.count("\x1b[2K")

    def flush(self):
        pass


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run(module, width):
    out = Sink()
    now = 0.0
    start = time.perf_counter()
    with patch("sys.stderr.isatty", return_value=True):
        progress = module.Progress(LABELS, stream=out, clock=lambda: now,
                                   env={"TERM": "xterm"}, terminal_width=lambda: width,
                                   terminal_height=lambda: 32)
        for i in range(len(LABELS)):
            progress.begin(i, module.Phase.CHECK)
            for j in range(40):
                if j % 10 == 0:
                    progress.substep(f"Verify operation {j//10+1}", module.Status.DONE)
                now += .15
                progress.refresh()
            progress.finish(module.Status.CACHED)
        progress.summary()
    return {"writes": out.writes, "bytes": out.bytes, "full_row_erases": out.erases,
            "renderer_ms": (time.perf_counter()-start)*1000}


def measure(module, width, repeats):
    rows = [run(module, width) for _ in range(repeats)]
    return {key: statistics.median(row[key] for row in rows) for key in rows[0]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", help="Local Git ref with a compatible Progress renderer")
    parser.add_argument("--width", type=int, default=120)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args(argv)
    if not 40 <= args.width <= 400 or not 1 <= args.repeats <= 100:
        parser.error("width must be 40..400 and repeats 1..100")
    current = load("progress_current", ROOT / RENDERER)
    result = {"scope": "synthetic renderer only", "width": args.width,
              "height": 32, "stages": len(LABELS), "refresh_requests_per_stage": 40,
              "repeats": args.repeats}
    if args.baseline:
        # Read only an explicitly selected, locally available Git object.
        # A ref is resolved before constructing the blob expression.
        sha = subprocess.check_output(
            ["git", "rev-parse", "--verify", "--end-of-options", args.baseline+"^{commit}"],
            cwd=ROOT, text=True).strip()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "renderer.py"
            path.write_bytes(subprocess.check_output(
                ["git", "show", sha+":"+RENDERER], cwd=ROOT))
            baseline = load("progress_baseline", path)
            result["baseline_commit"] = sha
            result["baseline"] = measure(baseline, args.width, args.repeats)
    result["current"] = measure(current, args.width, args.repeats)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
