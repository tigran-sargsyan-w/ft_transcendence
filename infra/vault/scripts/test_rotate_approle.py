"""Offline, fake-Vault tests; no credentials, Docker, or network required."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).with_name("rotate-approle.py")
spec = importlib.util.spec_from_file_location("approle_rotation", MODULE)
rotation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rotation)


class FakeVault:
    def __init__(self):
        self.secrets = {}
        self.issued = 0
        self.revoked = []
        self.fail_verify = False
        self.fail_lookup = False
        self.fail_destroy = False
        self.checked = []

    def role_id(self, role):
        return "role-id-for-" + role

    def lookup(self, role, secret):
        if self.fail_lookup or (role, secret) not in self.secrets:
            raise rotation.RotationError("lookup failed")
        return self.secrets[(role, secret)]

    def issue(self, role):
        self.issued += 1
        secret = f"secret-{self.issued}"
        accessor = f"accessor-{self.issued}"
        self.secrets[(role, secret)] = accessor
        return secret, accessor

    def verify(self, role, role_id, secret):
        if self.fail_verify or (role, secret) not in self.secrets:
            raise rotation.RotationError("scoped login or KV read failed")
        if role_id != self.role_id(role):
            raise rotation.RotationError("invalid role ID")
        self.checked.append((role, secret))

    def destroy(self, role, accessor):
        if self.fail_destroy:
            raise rotation.RotationError("revoke failed")
        keys = [k for k, v in self.secrets.items() if k[0] == role and v == accessor]
        if not keys:
            raise rotation.RotationError("no such accessor")
        for key in keys:
            del self.secrets[key]
        self.revoked.append((role, accessor))


class RotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.path.chmod(0o700)
        self.api = FakeVault()
        self.role = "backend-reader"
        self.patcher = patch("builtins.print")
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def data(self, name):
        return rotation._read(self.path / name)

    def old(self, role=None):
        role = role or self.role
        self.api.secrets[(role, "old-secret")] = "old-accessor"
        rotation._write(self.path / "role_id", self.api.role_id(role))
        rotation._write(self.path / "secret_id", "old-secret")

    def action(self, action, role=None):
        return rotation.operate(self.api, role or self.role, self.path, action)

    def test_first_time_provisioning_no_old_credential(self):
        self.action("rotate")
        self.assertEqual(self.data("secret_id"), "secret-1")
        self.assertIsNotNone(self.data(rotation.STATE))
        self.action("finalize")
        self.assertIsNone(self.data(rotation.STATE))
        self.assertEqual(self.api.revoked, [])

    def test_rotation_preserves_old_until_finalized(self):
        self.old()
        self.action("rotate")
        self.assertEqual(self.data("secret_id"), "secret-1")
        self.assertIn((self.role, "old-secret"), self.api.secrets)
        self.assertEqual(self.api.revoked, [])
        self.assertEqual(self.data("secret_id_accessor"), "accessor-1")
        self.action("finalize")
        self.assertNotIn((self.role, "old-secret"), self.api.secrets)
        self.assertEqual(self.api.revoked, [(self.role, "old-accessor")])
        self.assertIsNone(self.data(rotation.STATE))

    def test_cannot_start_second_rotation_while_pending(self):
        self.old()
        self.action("rotate")
        with self.assertRaisesRegex(rotation.RotationError, "Pending rotation"):
            self.action("rotate")
        self.assertEqual(self.api.issued, 1)

    def test_recover_requires_explicit_choice_if_old_secret_unavailable(self):
        self.old()
        del self.api.secrets[(self.role, "old-secret")]
        with self.assertRaisesRegex(rotation.RotationError, "explicit recover"):
            self.action("rotate")
        self.assertEqual(self.api.issued, 0)
        self.action("recover")
        self.action("finalize")
        self.assertEqual(self.api.revoked, [])
        self.assertEqual(self.data("secret_id"), "secret-1")

    def test_failed_candidate_verification_does_not_change_existing_secret(self):
        self.old()
        self.api.fail_verify = True
        with self.assertRaises(rotation.RotationError):
            self.action("rotate")
        self.assertEqual(self.data("secret_id"), "old-secret")
        self.assertIsNotNone(self.data(rotation.STATE))
        self.api.fail_verify = False
        self.action("abort")
        self.assertIsNone(self.data(rotation.STATE))
        self.assertEqual(self.api.revoked, [(self.role, "accessor-1")])

    def test_abort_refuses_to_destroy_installed_candidate(self):
        self.old()
        self.action("rotate")
        with self.assertRaisesRegex(rotation.RotationError, "already installed"):
            self.action("abort")
        self.assertEqual(self.api.revoked, [])
        self.action("finalize")

    def test_interrupted_finalization_can_be_retried(self):
        self.old()
        self.action("rotate")
        self.api.fail_destroy = True
        with self.assertRaises(rotation.RotationError):
            self.action("finalize")
        self.assertIsNotNone(self.data(rotation.STATE))
        self.api.fail_destroy = False
        self.action("finalize")
        self.assertIsNone(self.data(rotation.STATE))

    def test_mismatched_role_id_fails_before_issuing(self):
        self.old()
        rotation._write(self.path / "role_id", "wrong-role-id")
        with self.assertRaisesRegex(rotation.RotationError, "Stored RoleID"):
            self.action("rotate")
        self.assertEqual(self.api.issued, 0)

    def test_rejects_wrong_permissions(self):
        self.path.chmod(0o755)
        with self.assertRaisesRegex(rotation.RotationError, "group/others"):
            self.action("rotate")

    def test_separate_roles_have_separate_secrets_and_accessors(self):
        for role in rotation.ROLE_PATHS:
            with tempfile.TemporaryDirectory() as root:
                folder = Path(root)
                folder.chmod(0o700)
                rotation.operate(self.api, role, folder, "rotate")
                rotation.operate(self.api, role, folder, "finalize")
                self.assertIn((role, rotation._read(folder / "secret_id")), self.api.secrets)
        self.assertEqual(self.api.revoked, [])

    def test_candidate_never_written_to_json_state(self):
        self.old()
        self.action("rotate")
        state = self.data(rotation.STATE)
        self.assertNotIn("secret-1", state)
        self.assertEqual(json.loads(state)["new_accessor"], "accessor-1")
        self.assertEqual((self.path / "secret_id").stat().st_mode & 0o777, 0o400)


if __name__ == "__main__":
    unittest.main()
