"""Offline Grafana secret provisioning tests; no Docker or Vault access."""

import contextlib
import io
import subprocess
import unittest

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import bootstrap_grafana_secrets as driver
import grafana_secrets_helper as helper


def existing_secret():
    return {
        "admin_password": "a" * 64,
        "secret_key": "b" * 64,
    }


def response(data, version=1):
    return {
        "data": {
            "data": data,
            "metadata": {"version": version},
        },
    }


class FakeVault:
    def __init__(self, stored=None):
        self.stored = stored
        self.writes = []

    def request(self, *, payload=None):
        if payload is None:
            return self.stored

        if self.stored is not None:
            raise AssertionError("Unexpected existing secret overwrite")

        self.writes.append(payload)
        self.stored = response(dict(payload["data"]))
        return {}


class HelperTests(unittest.TestCase):
    def test_create_independent_credentials_with_cas_zero(self):
        client = FakeVault()

        self.assertEqual(helper.reconcile(client, "apply"), "created")
        self.assertEqual(len(client.writes), 1)

        payload = client.writes[0]

        self.assertEqual(payload["options"], {"cas": 0})
        self.assertEqual(set(payload["data"]), helper.FIELDS)

        for value in payload["data"].values():
            self.assertRegex(value, r"^[0-9a-f]{64}$")

        self.assertNotEqual(
            payload["data"]["admin_password"],
            payload["data"]["secret_key"],
        )

    def test_check_missing_does_not_write(self):
        client = FakeVault()

        self.assertEqual(helper.reconcile(client, "check"), "missing")
        self.assertEqual(client.writes, [])

    def test_existing_credentials_are_preserved(self):
        original = response(existing_secret())

        client = FakeVault(original)

        self.assertEqual(helper.reconcile(client, "apply"), "preserved")
        self.assertEqual(helper.reconcile(client, "check"), "preserved")
        self.assertEqual(client.writes, [])
        self.assertEqual(client.stored, original)

    def test_repeated_apply_never_rotates_credentials(self):
        client = FakeVault()

        self.assertEqual(helper.reconcile(client, "apply"), "created")
        stored = client.stored

        self.assertEqual(helper.reconcile(client, "apply"), "preserved")
        self.assertEqual(len(client.writes), 1)
        self.assertEqual(client.stored, stored)

    def test_invalid_secret_refuses_writes(self):
        invalid = [
            {"admin_password": "a" * 64},
            {"admin_password": "short", "secret_key": "b" * 64},
            {"admin_password": "a" * 64, "secret_key": "a" * 64},
            {**existing_secret(), "unexpected": "value"},
            {"admin_password": None, "secret_key": "b" * 64},
        ]

        for data in invalid:
            with self.subTest(fields=set(data)):
                client = FakeVault(response(data))

                with self.assertRaises(helper.SecretsError):
                    helper.reconcile(client, "apply")

                self.assertEqual(client.writes, [])

    def test_invalid_kv_version_refused(self):
        for version in (0, -1, True, "1"):
            with self.subTest(version=version):
                with self.assertRaises(helper.SecretsError):
                    helper.validate_stored(
                        response(existing_secret(), version)
                    )

    def test_write_readback_mismatch_refused(self):
        client = MagicMock()

        client.request.side_effect = [
            None,
            {},
            response(existing_secret()),
        ]

        with self.assertRaises(helper.SecretsError):
            helper.reconcile(client, "apply")

    def test_invalid_mode_refuses_vault_requests(self):
        client = MagicMock()

        with self.assertRaises(helper.SecretsError):
            helper.reconcile(client, "delete")

        client.request.assert_not_called()

    def test_http_redirects_are_refused(self):
        handler = helper.RejectRedirects()

        self.assertIsNone(
            handler.redirect_request(
                None, None, 302, "", {}, "https://example.invalid"
            )
        )

    def test_tls_client_disables_proxy(self):
        with patch.object(helper.ssl, "create_default_context"), \
             patch.object(helper, "ProxyHandler") as proxy, \
             patch.object(helper, "HTTPSHandler"), \
             patch.object(helper, "build_opener"):
            helper.VaultClient("test-token")

        proxy.assert_called_once_with({})


class DriverTests(unittest.TestCase):
    def test_plan_never_reads_credentials(self):
        with patch.object(driver.config, "recovery_token") as token, \
             patch.object(driver, "run_helper") as run, \
             patch.object(driver.config, "main") as config:
            self.assertEqual(driver.main(["plan"]), 0)

        token.assert_not_called()
        run.assert_not_called()
        config.assert_not_called()

    def test_helper_container_is_hardened(self):
        with patch.object(driver.Path, "is_file", return_value=True), \
             patch.object(driver.Path, "is_symlink", return_value=False):
            cmd = driver.helper_command("check")

        self.assertIn("--read-only", cmd)
        self.assertIn("--rm", cmd)
        self.assertIn("--interactive", cmd)
        self.assertIn("--cap-drop", cmd)
        self.assertIn("ALL", cmd)
        self.assertIn(driver.NETWORK, cmd)
        self.assertNotIn("--privileged", cmd)
        self.assertNotIn("VAULT_TOKEN", str(cmd))

    def test_operator_token_only_via_stdin(self):
        result = SimpleNamespace(
            returncode=0,
            stdout=(driver.MARKER + "preserved\n").encode(),
            stderr=b"",
        )

        with patch.object(driver, "helper_command", return_value=["docker"]), \
             patch.object(driver.subprocess, "run", return_value=result) as run:
            driver.run_helper("check", "private-test-token")

        args, kwargs = run.call_args

        self.assertEqual(kwargs["input"], b"private-test-token\n")
        self.assertNotIn("private-test-token", str(args))
        self.assertNotIn("private-test-token", str(kwargs["env"]))
        self.assertEqual(kwargs["timeout"], 30)

    def test_apply_rechecks_read_only(self):
        with patch.object(driver.config, "main", return_value=0), \
             patch.object(driver.config, "recovery_token", return_value="fake"), \
             patch.object(driver, "run_helper") as run:
            self.assertEqual(driver.main(["apply"]), 0)

        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            ["apply", "check"],
        )

    def test_vault_config_failure_blocks_provisioning(self):
        with patch.object(driver.config, "main", return_value=1), \
             patch.object(driver, "run_helper") as run, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(driver.main(["apply"]), 1)

        run.assert_not_called()

    def test_check_rejects_created_status(self):
        result = SimpleNamespace(
            returncode=0,
            stdout=(driver.MARKER + "created\n").encode(),
            stderr=b"",
        )

        with patch.object(driver, "helper_command", return_value=["docker"]), \
             patch.object(driver.subprocess, "run", return_value=result):
            with self.assertRaises(driver.GrafanaSecretsError):
                driver.run_helper("check", "fake")

    def test_missing_secrets_fail_read_only_check(self):
        result = SimpleNamespace(
            returncode=2,
            stdout=(driver.MARKER + "missing\n").encode(),
            stderr=b"",
        )

        with patch.object(driver, "helper_command", return_value=["docker"]), \
             patch.object(driver.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(driver.GrafanaSecretsError, "missing"):
                driver.run_helper("check", "fake")

    def test_provisioning_probe_distinguishes_missing_from_failure(self):
        with patch.object(driver.config, "main", return_value=0), \
             patch.object(driver.config, "recovery_token", return_value="fake"), \
             patch.object(driver, "run_helper", side_effect=driver.GrafanaSecretsMissing("missing")):
            self.assertEqual(driver.main(["check-missing"]), 2)
            self.assertEqual(driver.main(["check"]), 1)
        with patch.object(driver.config, "main", return_value=0), \
             patch.object(driver.config, "recovery_token", return_value="fake"), \
             patch.object(driver, "run_helper", side_effect=driver.GrafanaSecretsError("invalid")):
            self.assertEqual(driver.main(["check-missing"]), 1)

    def test_raw_helper_errors_are_never_disclosed(self):
        result = SimpleNamespace(
            returncode=1,
            stdout=b"private-test-token",
            stderr=b"private-test-token",
        )

        with patch.object(driver, "helper_command", return_value=["docker"]), \
             patch.object(driver.subprocess, "run", return_value=result):
            with self.assertRaises(driver.GrafanaSecretsError) as error:
                driver.run_helper("apply", "private-test-token")

        self.assertNotIn("private-test-token", str(error.exception))

    def test_helper_timeout_is_sanitized(self):
        with patch.object(driver, "helper_command", return_value=["docker"]), \
             patch.object(
                 driver.subprocess,
                 "run",
                 side_effect=subprocess.TimeoutExpired("private-token", 30),
             ):
            with self.assertRaises(driver.GrafanaSecretsError) as error:
                driver.run_helper("check", "fake")

        self.assertNotIn("private-token", str(error.exception))

    def test_duplicate_or_invalid_helper_results_refused(self):
        for output in (
            b"",
            b"FT_GRAFANA_SECRETS_RESULT=invalid\n",
            b"FT_GRAFANA_SECRETS_RESULT=created\n"
            b"FT_GRAFANA_SECRETS_RESULT=preserved\n",
        ):
            with self.subTest(output=output):
                with self.assertRaises(driver.GrafanaSecretsError):
                    driver.parse_result(output)


if __name__ == "__main__":
    unittest.main()
