"""Offline safety regressions for fresh development Vault setup."""
import contextlib
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).with_name("bootstrap_vault.py")
spec = importlib.util.spec_from_file_location("bootstrap_vault", MODULE)
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


class BootstrapVaultTests(unittest.TestCase):
    def valid_inspect(self):
        return {
            "Name": "/" + v.NAME,
            "Config": {"Labels": {
                "com.docker.compose.project": v.PROJECT,
                "com.docker.compose.service": "vault",
                "com.docker.compose.project.config_files": str(v.COMPOSE),
                "com.docker.compose.project.working_dir": str(v.COMPOSE.parent),
            }},
            "State": {"Running": True, "Status": "running"},
            "Mounts": [
                {"Destination": "/vault/data", "Type": "volume", "Name": v.PROJECT + "_vault_data"},
                {"Destination": "/vault/tls", "Type": "bind", "Source": str(v.STATE / "tls"), "RW": False},
            ],
            "NetworkSettings": {"Networks": {v.PROJECT + "_vault_private": {}}},
        }

    def test_absent_container_is_not_an_inspect_error(self):
        with patch.object(v, "call", return_value=b"") as run:
            self.assertIsNone(v.inspect_container())
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0][:4],
                             ["docker", "container", "ls", "--all"])

    def test_existing_container_is_inspected(self):
        data = self.valid_inspect()
        with patch.object(v, "call", side_effect=[
            (v.NAME + "\n").encode(), json.dumps([data]).encode(),
        ]) as run:
            self.assertEqual(v.inspect_container(), data)
            self.assertEqual(run.call_count, 2)

    def test_unexpected_container_inventory_is_rejected(self):
        with patch.object(v, "call", return_value=b"unexpected-name\n"):
            with self.assertRaisesRegex(v.VaultBootstrapError, "inventory"):
                v.inspect_container()

    def test_inspection_failure_not_treated_as_absent(self):
        with patch.object(v, "call", side_effect=[
            (v.NAME + "\n").encode(),
            v.VaultBootstrapError("Docker inspect failed"),
        ]):
            with self.assertRaisesRegex(v.VaultBootstrapError, "inspect failed"):
                v.inspect_container()

    def test_correct_compose_identity_accepted(self):
        self.assertIsNotNone(v.verify_identity(self.valid_inspect()))

    def test_unexpected_compose_identity_rejected(self):
        item = self.valid_inspect()
        item["Config"]["Labels"]["com.docker.compose.project.config_files"] = "/tmp/foreign.yml"
        with self.assertRaises(v.VaultBootstrapError):
            v.verify_identity(item)

    def test_wrong_data_volume_rejected(self):
        item = self.valid_inspect()
        item["Mounts"][0]["Name"] = "foreign_vault_data"
        with self.assertRaises(v.VaultBootstrapError):
            v.verify_identity(item)

    def test_existing_initialized_vault_does_not_reinit(self):
        state = {"initialized": True, "sealed": False, "n": 3, "t": 2,
                 "seal_type": "shamir", "storage_type": "raft"}
        with patch.object(v, "validate_secret_path"), patch.object(v, "start_or_keep"), \
             patch.object(v, "inspect_container", return_value=self.valid_inspect()), \
             patch.object(v, "status", return_value=state), \
             patch.object(v, "initialize_vault") as init:
            self.assertEqual(v.main(["apply"]), 0)
            init.assert_not_called()

    def test_sealed_existing_vault_is_not_auto_unsealed(self):
        state = {"initialized": True, "sealed": True, "n": 3, "t": 2,
                 "seal_type": "shamir", "storage_type": "raft"}
        with patch.object(v, "validate_secret_path"), patch.object(v, "start_or_keep"), \
             patch.object(v, "inspect_container", return_value=self.valid_inspect()), \
             patch.object(v, "status", return_value=state), \
             patch.object(v, "initialize_vault") as init:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(v.main(["apply"]), 1)
            init.assert_not_called()

    def test_recovery_file_blocks_second_initialization(self):
        with tempfile.TemporaryDirectory() as t:
            state = Path(t) / "state"
            path = state / "private/vault-init.json"
            path.parent.mkdir(parents=True, mode=0o700)
            path.write_text("old material")
            os.chmod(path, 0o600)
            with patch.object(v, "STATE", state), patch.object(v, "RECOVERY", path), \
                 patch.object(v, "json_result") as cmd:
                with self.assertRaisesRegex(v.VaultBootstrapError, "Recovery file exists|Protected recovery file exists"):
                    v.initialize_vault()
                cmd.assert_not_called()

    def test_initialize_writes_private_file_and_never_prints_secrets(self):
        with tempfile.TemporaryDirectory() as t:
            state = Path(t) / "state"
            path = state / "private/vault-init.json"
            payload = {"root_token": "secret-token-for-test", "unseal_keys_b64": ["share1", "share2", "share3"]}
            status = [
                {"initialized": True, "sealed": True, "progress": 1, "n": 3, "t": 2,
                 "seal_type": "shamir", "storage_type": "raft"},
                {"initialized": True, "sealed": False, "progress": 0, "n": 3, "t": 2,
                 "seal_type": "shamir", "storage_type": "raft"},
                {"initialized": True, "sealed": False, "progress": 0, "n": 3, "t": 2,
                 "seal_type": "shamir", "storage_type": "raft"},
            ]
            with patch.object(v, "STATE", state), patch.object(v, "RECOVERY", path), \
                 patch.object(v, "call", return_value=__import__("json").dumps(payload).encode()), \
                 patch.object(v, "json_result", side_effect=[{}, {}]), \
                 patch.object(v, "status", side_effect=status):
                stream = io.StringIO()
                with contextlib.redirect_stdout(stream):
                    v.initialize_vault()
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
                raw = path.read_text()
                self.assertIn("secret-token-for-test", raw)
                self.assertNotIn("secret-token-for-test", stream.getvalue())
                self.assertNotIn("share1", stream.getvalue())
                self.assertNotIn("share2", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
