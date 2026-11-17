"""Offline security and workflow tests for Phase 4A Vault configuration."""

import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bootstrap_vault_config as config


class VaultConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state = Path(self.tmp.name) / "state"
        private = state / "private"
        private.mkdir(parents=True, mode=0o700)
        private.chmod(0o700)
        path = private / "vault-init.json"
        path.write_text(json.dumps({"root_token": "root-token-SECRET", "unseal_keys_b64": ["a", "b", "c"]}))
        path.chmod(0o600)
        self.state = state
        self.path = path
        p1 = patch.object(config.vault, "STATE", state)
        p2 = patch.object(config.vault, "RECOVERY", path)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def test_recovery_token_not_echoed(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            token = config.recovery_token()
        self.assertEqual(token, "root-token-SECRET")
        self.assertNotIn(token, out.getvalue())

    def test_reject_unsafe_recovery_permissions(self):
        self.path.chmod(0o644)
        with self.assertRaises((config.ConfigError, config.vault.VaultBootstrapError)):
            config.recovery_token()

    def test_reject_unsafe_private_directory(self):
        self.path.parent.chmod(0o755)
        with self.assertRaises(config.ConfigError):
            config.recovery_token()

    def test_reject_recovery_symlink(self):
        self.path.unlink()
        other = Path(self.tmp.name) / "outside.json"
        other.write_text('{"root_token":"bad"}')
        self.path.symlink_to(other)
        with self.assertRaises(config.vault.VaultBootstrapError):
            config.recovery_token()

    def test_compose_helper_never_places_token_in_args(self):
        cmd = config.helper_command("apply")
        self.assertIn("--no-deps", cmd)
        self.assertIn("-T", cmd)
        self.assertIn("--env-file", cmd)
        self.assertIn("/dev/null", cmd)
        self.assertIn("FT_BOOTSTRAP_MODE=apply", cmd)
        self.assertNotIn("root-token-SECRET", str(cmd))
        self.assertNotIn("--privileged", cmd)

    def test_helper_token_is_only_stdin(self):
        with patch.object(config.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            config.run_helper("apply", "root-token-SECRET")
            args, kwargs = run.call_args
            self.assertEqual(kwargs["input"], b"root-token-SECRET\n")
            self.assertNotIn(b"root-token-SECRET", str(args).encode())
            self.assertNotIn("VAULT_TOKEN", kwargs["env"])
            self.assertNotIn("root-token-SECRET", str(kwargs["env"]))

    def test_helper_failure_never_prints_subprocess_output(self):
        result = SimpleNamespace(returncode=42, stdout=b"root-token-SECRET", stderr=b"root-token-SECRET")
        with patch.object(config.subprocess, "run", return_value=result), \
             contextlib.redirect_stdout(io.StringIO()), \
             self.assertRaises(config.ConfigError) as err:
            config.run_helper("check", "root-token-SECRET")
        self.assertNotIn("root-token-SECRET", str(err.exception))

    def test_plan_never_reads_token_or_runs_helper(self):
        with patch.object(config.vault, "main", return_value=0), \
             patch.object(config, "recovery_token") as token, \
             patch.object(config, "run_helper") as helper:
            self.assertEqual(config.main(["plan"]), 0)
            token.assert_not_called()
            helper.assert_not_called()

    def test_apply_rechecks_in_read_only_mode(self):
        with patch.object(config.vault, "main", return_value=0), \
             patch.object(config, "run_helper") as helper:
            self.assertEqual(config.main(["apply"]), 0)
            self.assertEqual(helper.call_count, 2)
            self.assertEqual(helper.call_args_list[0].args[0], "apply")
            self.assertEqual(helper.call_args_list[1].args[0], "check")

    def test_vault_not_ready_refuses_changes(self):
        with patch.object(config.vault, "main", return_value=1), \
             patch.object(config, "run_helper") as helper, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(config.main(["apply"]), 1)
            helper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
