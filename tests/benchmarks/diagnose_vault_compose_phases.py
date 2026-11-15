#!/usr/bin/env python3
"""Time read-only Vault Compose helper subprocess phases without leaking secrets.

This is an operator-run diagnostic, not part of normal startup. It invokes
bootstrap_vault_config.main(["check"]) on an already initialized development
VM. That established check reads the protected operator recovery token and
passes it only over stdin to its existing temporary Compose helper; this
script never receives, reads, prints, or stores the token itself.

It instruments only calls originating in bootstrap_vault_config.run_helper.
No subprocess argv, environment, stdin, stdout/stderr, or exception content
is logged. Phase durations overlap; never add them to compute wall time.
"""

import argparse
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def _in_vault_helper():
    # Inspects only Python stack code filenames and function names; no locals.
    try:
        frame = sys._getframe(1)
    except ValueError:
        return False
    for _ in range(12):
        if frame is None:
            break
        code = frame.f_code
        if (Path(code.co_filename).name == "bootstrap_vault_config.py"
                and code.co_name == "run_helper"):
            return True
        frame = frame.f_back
    return False


def _event(phase, state, elapsed=None):
    if elapsed is None:
        print(f"  PHASE {phase}: {state}", flush=True)
    else:
        print(f"  PHASE {phase}: {state} elapsed={max(0.0, elapsed):.2f}s", flush=True)


class PhasePopen(subprocess.Popen):
    """Pass-through Popen that times only allowlisted Vault config helper calls."""

    def __init__(self, *args, **kwargs):
        self._vault_trace = _in_vault_helper()
        started = time.monotonic()
        if self._vault_trace:
            _event("spawn", "BEGIN")
        try:
            super().__init__(*args, **kwargs)
        except BaseException:
            if self._vault_trace:
                _event("spawn", "ERROR", time.monotonic() - started)
            raise
        if self._vault_trace:
            _event("spawn", "END", time.monotonic() - started)

    def communicate(self, *args, **kwargs):
        if not self._vault_trace:
            return super().communicate(*args, **kwargs)
        started = time.monotonic()
        _event("communicate", "BEGIN")
        try:
            result = super().communicate(*args, **kwargs)
        except subprocess.TimeoutExpired:
            _event("communicate", "TIMEOUT", time.monotonic() - started)
            raise
        except BaseException:
            _event("communicate", "ERROR", time.monotonic() - started)
            raise
        _event("communicate", "END", time.monotonic() - started)
        return result

    def wait(self, *args, **kwargs):
        if not self._vault_trace:
            return super().wait(*args, **kwargs)
        started = time.monotonic()
        _event("wait", "BEGIN")
        try:
            result = super().wait(*args, **kwargs)
        except subprocess.TimeoutExpired:
            _event("wait", "TIMEOUT", time.monotonic() - started)
            raise
        except BaseException:
            _event("wait", "ERROR", time.monotonic() - started)
            raise
        _event("wait", "END", time.monotonic() - started)
        return result

    def kill(self):
        if not self._vault_trace:
            return super().kill()
        started = time.monotonic()
        _event("kill", "BEGIN")
        try:
            return super().kill()
        finally:
            _event("kill", "END", time.monotonic() - started)


def run_probe(config):
    """Invoke exactly the established, non-provisioning config check."""
    with patch.object(subprocess, "Popen", PhasePopen):
        return config.main(["check"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args(argv)
    if not 1 <= args.runs <= 6:
        parser.error("--runs must be 1..6")
    if not (ROOT / "scripts/bootstrap.sh").is_file():
        parser.error("Run from a checked-out ft_transcendence project")
    if not (Path.cwd() / "scripts/bootstrap.sh").is_file():
        parser.error("Run from the repository root")
    sys.path.insert(0, str(ROOT / "scripts/dev"))
    import bootstrap_vault_config as config

    print("=== Read-only Vault config helper process-phase diagnostic ===")
    print("Output excludes argv, stdin, env, Docker metadata, tokens and raw errors.", flush=True)
    for i in range(args.runs):
        started = time.monotonic()
        print(f"\nCheck {i + 1}/{args.runs}", flush=True)
        try:
            result = run_probe(config)
        except BaseException:
            # No exception contents: they could include sensitive diagnostics.
            _event("check", "ERROR", time.monotonic() - started)
            return 1
        _event("check", "PASS" if result == 0 else "FAIL", time.monotonic() - started)
        if result != 0:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
