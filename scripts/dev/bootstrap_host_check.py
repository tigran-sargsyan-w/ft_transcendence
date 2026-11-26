"""Present the three existing host-only checks without changing their order."""
from pathlib import Path
import subprocess
import sys

from . import bootstrap_cli_progress as cli
from .bootstrap_progress import Phase, Status, emit_result

ROOT = Path(__file__).resolve().parents[2]
CHECKS = (
    ("Development domain", "scripts.dev.domains"),
    ("Browser TLS certificate", "scripts.dev.certificates"),
    ("Local CA trust", "scripts.dev.ca_trust"),
)


class HostCheckError(Exception):
    def __init__(self, module, code):
        self.module = module
        self.code = code


def check():
    for label, module in CHECKS:
        with cli.stage(label, Phase.CHECK):
            try:
                # Same read-only module entry points; do not forward arbitrary
                # platform adapter output. The targeted command remains available.
                completed = subprocess.run([sys.executable, "-m", module, "check"],
                                           cwd=ROOT, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, check=False)
            except OSError:
                raise HostCheckError(module, 1) from None
            if completed.returncode:
                raise HostCheckError(module, completed.returncode)
    cli.result("HOST CHECK READY (read-only)")


def main():
    try:
        with cli.presentation(tuple(label for label, _ in CHECKS),
                              "Checking local host configuration"):
            check()
    except HostCheckError as exc:
        print(f"ERROR: Host check failed; inspect python3 -m {exc.module} check",
              file=sys.stderr, flush=True)
        emit_result("HOST CHECK FAILED (read-only)", Status.FAILED, stream=sys.stderr)
        return exc.code if exc.code > 0 else 128 - exc.code
    except KeyboardInterrupt:
        emit_result("HOST CHECK CANCELLED (read-only)", Status.CANCELLED, stream=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
