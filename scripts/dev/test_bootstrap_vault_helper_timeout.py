"""P8: helper checks fail promptly without shortening explicit provisioning.

Unit tests use mocks only: no Docker, Vault token access, or mutations.
"""

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bootstrap_vault_config as config
import bootstrap_vault_secrets as secrets


class ReadOnlyTimeoutContractTests(unittest.TestCase):
    def test_config_check_bounded_apply_unchanged(self):
        for mode, expected in (("check", 30), ("apply", 180)):
            with self.subTest(mode=mode), \
                 patch.object(config, "helper_command", return_value=["docker", "compose"]), \
                 patch.object(config.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
                config.run_helper(mode, "test-token")
            self.assertEqual(run.call_args.kwargs["timeout"], expected)
            self.assertEqual(run.call_args.kwargs["input"], b"test-token\n")
            self.assertNotIn("test-token", repr(run.call_args.args))

    def test_secrets_check_bounded_apply_unchanged(self):
        safe_reply = (secrets.MARKER + '{"backend":"preserved","migrator":"preserved"}\n').encode()
        for mode, expected in (("check", 30), ("apply", 180)):
            with self.subTest(mode=mode), \
                 patch.object(secrets, "helper_command", return_value=["docker", "run"]), \
                 patch.object(secrets.subprocess, "run", return_value=SimpleNamespace(
                     returncode=0, stdout=safe_reply, stderr=b"")) as run:
                secrets.run_helper(mode, "test-token")
            self.assertEqual(run.call_args.kwargs["timeout"], expected)
            self.assertEqual(run.call_args.kwargs["input"], b"test-token\n")
            self.assertNotIn("test-token", repr(run.call_args.args))

    def test_config_check_timeout_fails_closed_without_token_in_error(self):
        with patch.object(config, "helper_command", return_value=["docker", "compose"]), \
             patch.object(config.subprocess, "run", side_effect=subprocess.TimeoutExpired("docker", 30)):
            with self.assertRaises(config.ConfigError) as raised:
                config.run_helper("check", "test-token")
        self.assertNotIn("test-token", str(raised.exception))

    def test_secrets_check_timeout_fails_closed_without_token_in_error(self):
        with patch.object(secrets, "helper_command", return_value=["docker", "run"]), \
             patch.object(secrets.subprocess, "run", side_effect=subprocess.TimeoutExpired("docker", 30)):
            with self.assertRaises(secrets.SecretsBootstrapError) as raised:
                secrets.run_helper("check", "test-token")
        self.assertNotIn("test-token", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
