"""Verify native bootstrap command boundaries without Docker or host mutation."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "scripts/bootstrap.sh").read_text()
HOST_CHECK = SOURCE.split("host_check() {", 1)[1].split("\n}\n", 1)[0]
PREPARE = SOURCE.rsplit("  --prepare-host)\n", 1)[1].split("\n    ;;", 1)[0]
STUBS = r"""
say() { printf '%s\n' "$*"; }
fail() { say "ERROR: $*"; exit 1; }
python3() { printf 'PY %s\n' "$*" >&3; if [[ "$*" == *check-authorized && ${REFUSE:-0} == 1 ]]; then return 2; fi; }
vault_tls_status() { printf 'TLS_CHECK\n' >&3; return 0; }
stage_confirm() { [[ "$INITIALIZE_STAGE" == 1 ]]; }
exec 3>&1
"""


class BrowserTrustBootstrapTests(unittest.TestCase):
    def shell(self, body, *, native=1, refuse=0):
        command = STUBS + f"\nNATIVE_CA_TRUST={native}\nREFUSE={refuse}\nINITIALIZE_STAGE=1\n" + body
        return subprocess.run(["bash", "-euo", "pipefail", "-c", command],
                              capture_output=True, text=True, timeout=10)

    def test_native_initialize_calls_guarded_prepare_not_interactive_setup(self):
        result = self.shell("host_check() { return 0; }\n" + PREPARE)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [line for line in result.stdout.splitlines() if line.startswith("PY ")]
        self.assertEqual(calls, ["PY -m scripts.dev.vault_ca_trust preflight",
                                 "PY -m scripts.dev.bootstrap_linux_ca",
                                 "PY -m scripts.dev.nginx_tls_access apply",
                                 "PY -m scripts.dev.vault_ca_trust prepare"])
        self.assertNotIn("setup --apply", result.stdout)

    def test_full_host_check_requires_authorized_vault_trust(self):
        for refuse in (0, 1):
            with self.subTest(refuse=refuse):
                result = self.shell("host_check() {" + HOST_CHECK + "\n}\nhost_check", refuse=refuse)
                self.assertEqual(result.returncode, refuse)
                self.assertIn("PY -m scripts.dev.vault_ca_trust check-authorized", result.stdout)
                self.assertNotIn("prepare", result.stdout)

    def test_legacy_wsl_host_check_does_not_require_native_nss(self):
        result = self.shell("host_check() {" + HOST_CHECK + "\n}\nhost_check", native=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("vault_ca_trust", result.stdout)


if __name__ == "__main__":
    unittest.main()
