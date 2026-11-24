"""Offline Grafana AppRole provisioning tests."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infra/vault/scripts/grafana-auth.sh"


FAKE_VAULT = r"""#!/bin/sh
set -eu

case "$1:$2" in
    token:lookup)
        exit 0
        ;;
    read:-field=role_id)
        printf '%s' "test-role-id"
        ;;
    write:-force)
        printf '%s\n' issued >> "$TEST_VAULT_LOG"

        if [ "${TEST_FAIL_ISSUE:-0}" = 1 ]; then
            exit 1
        fi

        printf '%s' "test-secret-id"
        ;;
    *)
        exit 99
        ;;
esac
"""


class GrafanaAuthTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)

        self.root = Path(tmp.name)
        self.auth = self.root / "auth"
        self.auth.mkdir(mode=0o700)

        self.log = self.root / "vault-writes.log"
        self.log.write_text("")

        fake = self.root / "vault"
        fake.write_text(FAKE_VAULT)
        fake.chmod(0o700)

        # Adapt filesystem ownership assertions to the current offline
        # test user. Production source remains unchanged.
        source = SCRIPT.read_text()
        source = source.replace("/vault/auth", str(self.auth))
        source = source.replace(
            "100:1000:",
            f"{os.getuid()}:{os.getgid()}:",
        )

        self.script = self.root / "auth.sh"
        self.script.write_text(source)

    def run_script(self, mode, fail_issue=False):
        env = {
            **os.environ,
            "PATH": str(self.root) + ":" + os.environ["PATH"],
            "TEST_VAULT_LOG": str(self.log),
            "TEST_FAIL_ISSUE": "1" if fail_issue else "0",
        }

        return subprocess.run(
            ["sh", str(self.script), mode],
            input="private-test-token\n",
            text=True,
            env=env,
            capture_output=True,
            timeout=10,
        )

    def write_credentials(self):
        for name in ("role_id", "secret_id"):
            path = self.auth / name
            path.write_text("existing-" + name)
            path.chmod(0o400)

    def test_missing_check_does_not_issue_credentials(self):
        result = self.run_script("check")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.log.read_text(), "")
        self.assertEqual(list(self.auth.iterdir()), [])

    def test_first_apply_creates_restricted_credentials(self):
        result = self.run_script("apply")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text(), "issued\n")

        for name in ("role_id", "secret_id"):
            path = self.auth / name

            self.assertTrue(path.is_file())
            self.assertEqual(path.stat().st_mode & 0o777, 0o400)

        self.assertFalse(
            (self.auth / ".provisioning-pending").exists()
        )

    def test_repeated_apply_preserves_credentials(self):
        self.write_credentials()

        before = {
            path.name: path.read_bytes()
            for path in self.auth.iterdir()
        }

        self.assertEqual(self.run_script("apply").returncode, 0)
        self.assertEqual(self.run_script("check").returncode, 0)

        self.assertEqual(self.log.read_text(), "")

        self.assertEqual(before, {
            path.name: path.read_bytes()
            for path in self.auth.iterdir()
        })

    def test_partial_credentials_refuse_apply(self):
        self.write_credentials()
        (self.auth / "secret_id").unlink()

        self.assertNotEqual(self.run_script("apply").returncode, 0)
        self.assertEqual(self.log.read_text(), "")

    def test_unsafe_permissions_refuse_apply(self):
        self.write_credentials()
        (self.auth / "secret_id").chmod(0o644)

        self.assertNotEqual(self.run_script("apply").returncode, 0)
        self.assertEqual(self.log.read_text(), "")

    def test_unsafe_directory_permissions_refused(self):
        self.auth.chmod(0o755)

        self.assertNotEqual(self.run_script("apply").returncode, 0)
        self.assertEqual(self.log.read_text(), "")

    def test_symlink_credentials_refused(self):
        self.write_credentials()

        path = self.auth / "secret_id"
        path.unlink()
        path.symlink_to(self.root / "missing")

        self.assertNotEqual(self.run_script("apply").returncode, 0)
        self.assertEqual(self.log.read_text(), "")

    def test_interrupted_provisioning_blocks_repeated_issuance(self):
        result = self.run_script("apply", fail_issue=True)

        self.assertNotEqual(result.returncode, 0)

        self.assertTrue(
            (self.auth / ".provisioning-pending").exists()
        )

        self.assertNotEqual(self.run_script("apply").returncode, 0)
        self.assertEqual(self.log.read_text(), "issued\n")

    def test_no_credential_values_in_output(self):
        result = self.run_script("apply")

        self.assertEqual(result.returncode, 0)

        output = result.stdout + result.stderr

        for value in (
            "private-test-token",
            "test-role-id",
            "test-secret-id",
        ):
            self.assertNotIn(value, output)


if __name__ == "__main__":
    unittest.main()
