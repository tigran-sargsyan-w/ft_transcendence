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
        if self.fail_lookup:
            raise rotation.RotationError("Vault network request failed")
        if (role, secret) not in self.secrets:
            raise rotation.VaultHTTPError(400, "auth/approle/secret-id/lookup")
        return self.secrets[(role, secret)]

    def list_accessors(self, role):
        return [
            accessor
            for (secret_role, _), accessor in self.secrets.items()
            if secret_role == role
        ]

    def issue(self, role):
        self.issued += 1
        secret = f"secret-{self.issued}"
        accessor = f"accessor-{self.issued}"
        self.secrets[(role, secret)] = accessor
        return secret, accessor

    def destroy_if_present(self, role, accessor):
        if (role, accessor) in self.revoked:
            return
        self.destroy(role, accessor)

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

    def test_revocation_confirmed_absent_is_idempotent(self):
        api = rotation.VaultAPI.__new__(rotation.VaultAPI)
        calls = []
        def request(method, path, data=None, token=None):
            calls.append(path)
            if path.endswith("/destroy"):
                raise rotation.VaultHTTPError(404, path)
            if path.endswith("/lookup"):
                raise rotation.VaultHTTPError(404, path)
        api.request = request
        api.destroy_if_present(self.role, "old-accessor")
        self.assertEqual(len(calls), 2)

    def test_revoke_does_not_ignore_authorization_error(self):
        api = rotation.VaultAPI.__new__(rotation.VaultAPI)
        def request(method, path, data=None, token=None):
            raise rotation.VaultHTTPError(403, path)
        api.request = request
        with self.assertRaises(rotation.VaultHTTPError):
            api.destroy_if_present(self.role, "old-accessor")

    def test_revocation_not_assumed_if_accessor_still_exists(self):
        api = rotation.VaultAPI.__new__(rotation.VaultAPI)
        def request(method, path, data=None, token=None):
            if path.endswith("/destroy"):
                raise rotation.VaultHTTPError(400, path)
            return {"data": {"secret_id_accessor": "old-accessor"}}
        api.request = request
        with self.assertRaisesRegex(rotation.RotationError, "not confirmed"):
            api.destroy_if_present(self.role, "old-accessor")

    def test_empty_lookup_raises_missing_secret_error(self):
        api = rotation.VaultAPI.__new__(rotation.VaultAPI)
        api.request = lambda *args, **kwargs: {"data": None}

        with self.assertRaises(rotation.MissingSecretIDLookup):
            api.lookup(self.role, "revoked-secret")

    def test_empty_lookup_with_active_accessor_blocks_recover(self):
        self.old()
        rotation._write(self.path / "secret_id_accessor", "old-accessor")

        with patch.object(
            self.api,
            "lookup",
            side_effect=rotation.MissingSecretIDLookup("no data"),
        ):
            with self.assertRaisesRegex(
                rotation.RotationError, "still active"
            ):
                self.action("recover")

        self.assertEqual(self.api.issued, 0)
        self.assertEqual(self.data("secret_id"), "old-secret")

    def test_empty_lookup_requires_explicit_recover(self):
        self.old()
        rotation._write(self.path / "secret_id_accessor", "old-accessor")
        del self.api.secrets[(self.role, "old-secret")]

        with patch.object(
            self.api,
            "lookup",
            side_effect=rotation.MissingSecretIDLookup("no data"),
        ):
            with self.assertRaisesRegex(
                rotation.RotationError, "explicit recover"
            ):
                self.action("rotate")

            self.assertEqual(self.api.issued, 0)
            self.action("recover")

        self.assertEqual(self.api.issued, 1)
        self.action("finalize")
        self.assertIsNone(self.data(rotation.STATE))
        self.assertEqual(self.data("secret_id"), "secret-1")

    def test_recover_rejects_untracked_active_accessors(self):
        self.old()
        del self.api.secrets[(self.role, "old-secret")]
        self.api.secrets[(self.role, "unknown-secret")] = "unknown-accessor"

        with patch.object(
            self.api,
            "lookup",
            side_effect=rotation.MissingSecretIDLookup("no data"),
        ):
            with self.assertRaisesRegex(
                rotation.RotationError, "Other active SecretIDs"
            ):
                self.action("recover")

        self.assertEqual(self.api.issued, 0)

    def test_accessor_listing_404_requires_existing_role(self):
        api = rotation.VaultAPI.__new__(rotation.VaultAPI)

        def missing_list(method, path, data=None, token=None):
            raise rotation.VaultHTTPError(404, path)

        api.request = missing_list
        api.role_id = lambda role: "valid-role-id"

        self.assertEqual(api.list_accessors(self.role), [])

        def missing_role(role):
            raise rotation.VaultHTTPError(404, "role-id")

        api.role_id = missing_role

        with self.assertRaises(rotation.VaultHTTPError):
            api.list_accessors(self.role)

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
        with self.assertRaisesRegex(rotation.RotationError, "Auth file changed"):
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

    def test_abort_handles_expired_old_secret_after_candidate_validation_fails(self):
        self.old()
        del self.api.secrets[(self.role, "old-secret")]
        self.api.fail_verify = True
        with self.assertRaises(rotation.RotationError):
            self.action("recover")
        self.assertEqual(self.data("secret_id"), "old-secret")
        self.api.fail_verify = False
        self.action("abort")
        self.assertIsNone(self.data(rotation.STATE))
        self.assertEqual(self.api.revoked, [(self.role, "accessor-1")])

    def test_abort_fails_closed_if_installed_candidate_already_invalid(self):
        self.old()
        self.action("rotate")
        del self.api.secrets[(self.role, "secret-1")]
        with self.assertRaisesRegex(rotation.RotationError, "Auth file changed"):
            self.action("abort")
        self.assertIsNotNone(self.data(rotation.STATE))

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

    def test_recover_never_ignores_network_errors(self):
        self.old()
        self.api.fail_lookup = True
        with self.assertRaisesRegex(rotation.RotationError, "network request failed"):
            self.action("recover")
        self.assertEqual(self.api.issued, 0)
        self.assertEqual(self.data("secret_id"), "old-secret")

    def test_recover_never_ignores_forbidden_or_server_error(self):
        self.old()
        original_lookup = self.api.lookup
        for status in (401, 403, 500, 503):
            self.api.lookup = lambda role, secret, status=status: (_ for _ in ()).throw(
                rotation.VaultHTTPError(status, "auth/approle/secret-id/lookup")
            )
            with self.assertRaises(rotation.VaultHTTPError):
                self.action("recover")
            self.assertEqual(self.api.issued, 0)
        self.api.lookup = original_lookup

    def test_journal_write_failure_revokes_untracked_candidate(self):
        self.old()
        original_write = rotation._write
        def broken_journal(path, value):
            if path.name == rotation.STATE:
                raise OSError("simulated disk error")
            return original_write(path, value)
        with patch.object(rotation, "_write", side_effect=broken_journal):
            with self.assertRaisesRegex(rotation.RotationError, "unused candidate revoked"):
                self.action("rotate")
        self.assertEqual(self.data("secret_id"), "old-secret")
        self.assertIsNone(self.data(rotation.STATE))
        self.assertEqual(self.api.revoked, [(self.role, "accessor-1")])

    def test_verify_uses_operator_token_to_revoke_validation_token(self):
        api = rotation.VaultAPI.__new__(rotation.VaultAPI)
        api.admin_token = "operator-token"
        calls = []
        def request(method, path, data=None, token=None):
            calls.append((method, path, data, token))
            if path == "auth/approle/login":
                return {"auth": {"client_token": "validation-token", "token_policies": ["backend-readonly"]}}
            if path.startswith("kv/data/"):
                return {"data": {"data": {"database_url": "fake-connection-url"}}}
            if path == "auth/token/revoke":
                return {}
            raise AssertionError("Unexpected request")
        api.request = request
        api.verify("backend-reader", "role-id", "secret-id")
        self.assertEqual(calls[-1], (
            "POST", "auth/token/revoke", {"token": "validation-token"}, None,
        ))
        self.assertFalse(any(path == "auth/token/revoke-self" for _, path, _, _ in calls))

    def test_candidate_never_written_to_json_state(self):
        self.old()
        self.action("rotate")
        state = self.data(rotation.STATE)
        self.assertNotIn("secret-1", state)
        self.assertEqual(json.loads(state)["new_accessor"], "accessor-1")
        self.assertEqual((self.path / "secret_id").stat().st_mode & 0o777, 0o400)


if __name__ == "__main__":
    unittest.main()
