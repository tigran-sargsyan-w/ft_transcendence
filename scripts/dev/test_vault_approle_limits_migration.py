"""Offline regression tests for explicitly authorized DEV AppRole limit migration.

Executes the real role-maintenance shell functions against a fake Vault CLI.
Never connects to Docker/Vault, reads tokens or changes runtime credentials.
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infra/vault/server/bootstrap.sh"
ROLES = {
    "backend-reader": "backend-readonly",
    "migrator-reader": "migrator-readonly",
    "postgres-provisioner-reader": "postgres-provisioner-readonly",
    "grafana-reader": "grafana-readonly",
}

FAKE_VAULT = r'''#!/bin/sh
set -eu
operation="$1"
endpoint="$2"
if [ "$operation" = read ] && [ "$endpoint" = sys/auth/approle/tune ]; then
  printf 'max_lease_ttl %s\n' "$(cat "$FT_TEST_MOUNT")"
  exit 0
fi
if [ "$operation" = auth ] && [ "$endpoint" = tune ]; then
  [ "$3" = -max-lease-ttl=2160h ] && [ "$4" = approle/ ] || exit 95
  printf '2160h\n' > "$FT_TEST_MOUNT"
  printf '%s\n' sys/auth/approle/tune >> "$FT_TEST_LOG"
  exit 0
fi
role="$(printf '%s' "$endpoint" | cut -d / -f 4)"
file="$FT_TEST_STATE/$role"
case "$operation" in
  read)
    if [ ! -f "$file" ]; then
      printf 'No value found at %s\n' "$endpoint"
      exit 2
    fi
    awk -F= '{ if ($1 == "token_policies") print $1 " [" $2 "]";
                else print $1 " " $2 }' "$file"
    ;;
  write)
    if [ "${FT_TEST_FAIL_WRITE:-}" = "$endpoint" ]; then exit 80; fi
    shift 2
    case "$endpoint" in
      */secret-id-num-uses)
        [ "$#" -eq 1 ] && [ "$1" = secret_id_num_uses=0 ] || exit 91
        sed -i 's/^secret_id_num_uses=.*/secret_id_num_uses=0/' "$file"
        ;;
      */secret-id-ttl)
        [ "$#" -eq 1 ] && [ "$1" = secret_id_ttl=2160h ] || exit 92
        sed -i 's/^secret_id_ttl=.*/secret_id_ttl=2160h/' "$file"
        ;;
      *)
        [ ! -f "$file" ] || exit 93
        printf '%s\n' "$@" > "$file"
        ;;
    esac
    printf '%s\n' "$endpoint" >> "$FT_TEST_LOG"
    ;;
  *) exit 94 ;;
esac
'''


def initial_state(uses="20", ttl="168h"):
    return {
        role: {
            "token_policies": policy,
            "bind_secret_id": "true",
            "token_no_default_policy": "true",
            "secret_id_ttl": ttl,
            "secret_id_num_uses": uses,
            "token_ttl": "1h",
            "token_max_ttl": "4h",
        }
        for role, policy in ROLES.items()
    }


class AppRoleLimitTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "roles"
        self.state.mkdir()
        self.log = self.root / "writes.log"
        self.mount = self.root / "mount-ttl"
        self.fake = self.root / "fake_vault.sh"
        self.fake.write_text(FAKE_VAULT)

        source = SCRIPT.read_text()
        self.assertIn("SECRET_ID_USES=0", source)
        self.assertIn("SECRET_ID_TTL=2160h", source)
        shell_helpers = source[source.index("read_field() {"):source.index("\nensure_role backend-reader")]
        self.script = self.root / "check.sh"
        self.script.write_text(textwrap.dedent('''\
            #!/bin/sh
            set -eu
            MODE="${FT_TEST_MODE:-check}"
            SECRET_ID_USES=0
            SECRET_ID_TTL=2160h
            PREVIOUS_SECRET_ID_USES=20
            PREVIOUS_SECRET_ID_TTL=168h
            fail() { echo "ERROR: $*" >&2; exit 1; }
            is_check() { [ "$MODE" = check ]; }
            vault() { sh "$FT_TEST_FAKE" "$@"; }
        ''') + shell_helpers + "\n" + "\n".join(
            f"ensure_role {role} {policy}" for role, policy in ROLES.items()
        ) + "\nensure_approle_mount_limit\n")

    def seed(self, state, mount_ttl="768h"):
        for role in ROLES:
            file = self.state / role
            if state[role] is None:
                file.unlink(missing_ok=True)
            else:
                file.write_text("".join(f"{k}={v}\n" for k, v in state[role].items()))
        self.mount.write_text(mount_ttl + "\n")
        self.log.write_text("")

    def run_script(self, mode, fail_write=""):
        env = {**os.environ, "FT_TEST_STATE": str(self.state),
               "FT_TEST_LOG": str(self.log), "FT_TEST_FAKE": str(self.fake),
               "FT_TEST_MODE": mode, "FT_TEST_FAIL_WRITE": fail_write,
               "FT_TEST_MOUNT": str(self.mount)}
        return subprocess.run(["sh", str(self.script)], env=env,
                              capture_output=True, text=True, timeout=20)

    def read(self):
        result = {}
        for role in ROLES:
            file = self.state / role
            result[role] = (dict(line.split("=", 1) for line in file.read_text().splitlines())
                            if file.exists() else None)
        return result

    def writes(self):
        return self.log.read_text().splitlines()

    def test_check_refuses_legacy_limits_without_mutation(self):
        original = initial_state()
        self.seed(original)
        result = self.run_script("check")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.read(), original)
        self.assertEqual(self.writes(), [])

    def test_apply_migrates_only_two_fields_and_check_passes(self):
        original = initial_state()
        self.seed(original)
        result = self.run_script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.writes()), 9)
        for role, old in original.items():
            new = self.read()[role]
            self.assertEqual(new["secret_id_num_uses"], "0")
            self.assertEqual(new["secret_id_ttl"], "2160h")
            for key in ("token_policies", "bind_secret_id", "token_no_default_policy",
                        "token_ttl", "token_max_ttl"):
                self.assertEqual(new[key], old[key])
        self.assertEqual(self.run_script("check").returncode, 0)
        self.assertEqual(len(self.writes()), 9)

    def test_apply_is_idempotent(self):
        self.seed(initial_state("0", "2160h"), mount_ttl="2160h")
        result = self.run_script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.writes(), [])

    def test_partial_upgrade_can_be_safely_resumed(self):
        state = initial_state("0", "2160h")
        state["backend-reader"]["secret_id_ttl"] = "168h"
        state["migrator-reader"]["secret_id_num_uses"] = "20"
        self.seed(state)
        result = self.run_script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.writes()), 3)
        self.assertEqual(self.run_script("check").returncode, 0)

    def test_unsupported_drift_fails_closed_before_writing_role(self):
        state = initial_state()
        state["backend-reader"]["secret_id_num_uses"] = "99"
        self.seed(state)
        result = self.run_script("apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.read(), state)

    def test_security_invariants_refuse_writes(self):
        for key, changed in (("bind_secret_id", "false"),
                             ("token_policies", "root"),
                             ("token_max_ttl", "24h")):
            with self.subTest(key=key):
                state = initial_state()
                state["backend-reader"][key] = changed
                self.seed(state)
                result = self.run_script("apply")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.read(), state)
                self.assertEqual(self.writes(), [])

    def test_write_failure_stops_with_recoverable_partial_state(self):
        self.seed(initial_state())
        failing_path = "auth/approle/role/backend-reader/secret-id-ttl"
        result = self.run_script("apply", fail_write=failing_path)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.writes(), ["auth/approle/role/backend-reader/secret-id-num-uses"])
        self.assertEqual(self.run_script("apply").returncode, 0)
        self.assertEqual(self.run_script("check").returncode, 0)

    def test_mount_old_limit_check_fails_without_write(self):
        self.seed(initial_state("0", "2160h"), mount_ttl="768h")
        result = self.run_script("check")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.mount.read_text().strip(), "768h")
        self.assertEqual(self.writes(), [])

    def test_mount_unknown_drift_refused(self):
        self.seed(initial_state("0", "2160h"), mount_ttl="720h")
        result = self.run_script("apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.mount.read_text().strip(), "720h")
        self.assertEqual(self.writes(), [])

    def test_fresh_role_uses_new_values(self):
        self.seed({role: None for role in ROLES})
        result = self.run_script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.writes()), 5)
        self.assertTrue(all("secret-id" not in p for p in self.writes()))
        self.assertEqual(self.run_script("check").returncode, 0)



    def test_existing_vault_adds_only_missing_grafana_role(self):
        original = initial_state("0", "2160h")
        original["grafana-reader"] = None

        self.seed(original, mount_ttl="2160h")

        result = self.run_script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertEqual(
            self.writes(),
            ["auth/approle/role/grafana-reader"],
        )

        expected = initial_state("0", "2160h")
        self.assertEqual(self.read(), expected)

        self.assertEqual(self.run_script("check").returncode, 0)
        self.assertEqual(len(self.writes()), 1)

    def test_missing_grafana_role_check_never_creates_it(self):
        original = initial_state("0", "2160h")
        original["grafana-reader"] = None

        self.seed(original, mount_ttl="2160h")

        result = self.run_script("check")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.read(), original)
        self.assertEqual(self.writes(), [])

    def test_existing_grafana_policy_drift_is_refused(self):
        original = initial_state("0", "2160h")
        original["grafana-reader"]["token_policies"] = "root"

        self.seed(original, mount_ttl="2160h")

        result = self.run_script("apply")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.read(), original)
        self.assertEqual(self.writes(), [])

    def test_grafana_policy_is_scoped_to_its_own_secret(self):
        import re

        policy = (
            ROOT / "infra/vault/policies/grafana-readonly.hcl"
        ).read_text()

        actual = dict(re.findall(
            r'path\s+"([^"]+)"\s*\{\s*'
            r'capabilities\s*=\s*\["([^"]+)"\]\s*\}',
            policy,
        ))

        self.assertEqual(actual, {
            "kv/data/ft-transcendence/dev/grafana": "read",
            "auth/token/renew-self": "update",
            "auth/token/lookup-self": "read",
        })

        self.assertEqual(
            len(re.findall(r'\bpath\s+"', policy)),
            3,
        )


if __name__ == "__main__":
    unittest.main()
