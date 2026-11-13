"""Offline safety regressions for Phase 4B Vault PostgreSQL secrets."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/postgres"))

import bootstrap_vault_secrets as driver
import vault_secrets_helper as helper


class DriverTests(unittest.TestCase):
    def test_plan_does_not_access_root_token_or_vault(self):
        with patch.object(driver.config, "main") as check, \
             patch.object(driver.config, "recovery_token") as token, \
             patch.object(driver, "run_helper") as run:
            self.assertEqual(driver.main(["plan"]), 0)
            check.assert_not_called()
            token.assert_not_called()
            run.assert_not_called()

    def test_helper_args_are_network_isolated_and_token_free(self):
        with tempfile.TemporaryDirectory() as td:
            paths = [Path(td) / part for part in ("helper", "contract", "ca")]
            for path in paths:
                path.write_text("nonsecret")
            with patch.object(driver, "HELPER", paths[0]), \
                 patch.object(driver, "CONTRACT", paths[1]), \
                 patch.object(driver, "CA_FILE", paths[2]):
                command = driver.helper_command("apply")
        for required in ("--rm", "--read-only", "--interactive", "--network", "--cap-drop"):
            self.assertIn(required, command)
        self.assertIn(driver.NETWORK, command)
        self.assertIn("--user", command)
        self.assertIn("65534:65534", command)
        self.assertNotIn("--privileged", command)
        self.assertNotIn("root-token", repr(command))

    def test_helper_token_only_via_stdin(self):
        status = (driver.MARKER + '{"backend":"created","migrator":"preserved"}\n').encode()
        with patch.object(driver, "helper_command", return_value=["docker", "run", "image"]), \
             patch.object(driver.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=status, stderr=b"")) as run:
            driver.run_helper("apply", "root-token-secret")
        args, kwargs = run.call_args
        self.assertEqual(kwargs["input"], b"root-token-secret\n")
        self.assertNotIn("root-token-secret", str(args))
        self.assertNotIn("root-token-secret", str(kwargs["env"]))
        self.assertNotIn("VAULT_TOKEN", kwargs["env"])

    def test_raw_failure_output_never_disclosed(self):
        with patch.object(driver, "helper_command", return_value=["docker", "run"]), \
             patch.object(driver.subprocess, "run", return_value=SimpleNamespace(
                 returncode=1, stdout=b"root-token-secret database_url=secret", stderr=b"root-token-secret")):
            with self.assertRaises(driver.SecretsBootstrapError) as raised:
                driver.run_helper("apply", "root-token-secret")
        self.assertNotIn("root-token-secret", str(raised.exception))
        self.assertNotIn("database_url=", str(raised.exception))

    def test_untrusted_helper_status_rejected(self):
        for output in (b"", b"database_url=secret", (driver.MARKER + '{"backend":"secret","migrator":"preserved"}').encode()):
            with self.subTest(output=output):
                with self.assertRaises(driver.SecretsBootstrapError):
                    driver.parse_result(output)

    def test_missing_secret_check_fails_closed(self):
        status = (driver.MARKER + '{"backend":"missing","migrator":"preserved"}').encode()
        with patch.object(driver, "helper_command", return_value=["docker", "run"]), \
             patch.object(driver.subprocess, "run", return_value=SimpleNamespace(returncode=2, stdout=status, stderr=b"")):
            with self.assertRaisesRegex(driver.SecretsBootstrapError, "missing"):
                driver.run_helper("check", "root-token-secret")

    def test_apply_then_readonly_check(self):
        with patch.object(driver.config, "main", return_value=0), \
             patch.object(driver.config, "recovery_token", return_value="secret"), \
             patch.object(driver, "run_helper") as run:
            self.assertEqual(driver.main(["apply"]), 0)
            self.assertEqual([c.args[0] for c in run.call_args_list], ["apply", "check"])

    def test_config_failure_blocks_changes(self):
        with patch.object(driver.config, "main", return_value=1), \
             patch.object(driver, "run_helper") as run, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(driver.main(["apply"]), 1)
            run.assert_not_called()


class FakeVault:
    def __init__(self, missing=()):
        self.values = {}
        for purpose, name in helper.ROLES.items():
            if purpose not in missing:
                self.values[purpose] = f"postgresql://{name}:prior-password@postgres:5432/transcendence"
        self.writes = []

    def request(self, purpose, *, payload=None):
        if payload is None:
            url = self.values.get(purpose)
            if url is None:
                return None
            return {"data": {"data": {"database_url": url}, "metadata": {"version": 1}}}
        self.writes.append((purpose, payload))
        if purpose in self.values:
            raise AssertionError("Test attempted overwriting an existing secret")
        self.values[purpose] = payload["data"]["database_url"]
        return {"data": {"version": 1}}


class HelperTests(unittest.TestCase):
    def test_check_has_no_side_effects(self):
        client = FakeVault(missing=("backend", "migrator"))
        with patch.object(helper.secrets, "token_urlsafe") as make_password:
            states = helper.reconcile(client, "check")
        self.assertEqual(states, {"backend": "missing", "migrator": "missing"})
        self.assertEqual(client.writes, [])
        make_password.assert_not_called()

    def test_existing_credentials_are_never_rotated(self):
        client = FakeVault()
        self.assertEqual(helper.reconcile(client, "apply"), {"backend": "preserved", "migrator": "preserved"})
        self.assertEqual(client.writes, [])

    def test_create_two_distinct_credentials_cas_zero(self):
        client = FakeVault(missing=("backend", "migrator"))
        with patch.object(helper.secrets, "token_urlsafe", side_effect=["strong-one", "strong-two"]) as make:
            states = helper.reconcile(client, "apply")
        self.assertEqual(states, {"backend": "created", "migrator": "created"})
        self.assertEqual(make.call_count, 2)
        self.assertEqual(len(client.writes), 2)
        self.assertTrue(all(payload["options"] == {"cas": 0} for _, payload in client.writes))
        self.assertIn("strong-one", client.values["backend"])
        self.assertIn("strong-two", client.values["migrator"])
        # Re-execution is idempotent.
        self.assertEqual(helper.reconcile(client, "apply"), {"backend": "preserved", "migrator": "preserved"})
        self.assertEqual(len(client.writes), 2)

    def test_partial_recovery_only_creates_missing_secret(self):
        client = FakeVault(missing=("migrator",))
        self.assertEqual(helper.reconcile(client, "apply"), {"backend": "preserved", "migrator": "created"})
        self.assertEqual([p for p, _ in client.writes], ["migrator"])

    def test_new_secret_must_match_written_value(self):
        class AlteringVault(FakeVault):
            def request(self, purpose, *, payload=None):
                if payload is not None:
                    super().request(purpose, payload=payload)
                    # Keep the DSN valid but simulate a concurrent update.
                    self.values[purpose] += "&changed=1"
                    return {"data": {"version": 1}}
                return super().request(purpose)
        with self.assertRaisesRegex(helper.SecretsError, "verification mismatch"):
            helper.reconcile(AlteringVault(missing=("backend", "migrator")), "apply")

    def test_invalid_existing_secret_refuses_any_changes(self):
        client = FakeVault(missing=("migrator",))
        client.values["backend"] = "postgresql://wrong:password@postgres:5432/transcendence"
        with self.assertRaisesRegex(helper.SecretsError, "backend"):
            helper.reconcile(client, "apply")
        self.assertEqual(client.writes, [])

    def test_http_failure_does_not_reveal_secret(self):
        class RefusingVault(FakeVault):
            def request(self, purpose, *, payload=None):
                if payload is not None:
                    raise helper.SecretsError("Vault POST rejected (HTTP 403)")
                return None
        with self.assertRaises(helper.SecretsError) as raised:
            helper.reconcile(RefusingVault(), "apply")
        self.assertNotIn("database_url", str(raised.exception))

    def test_contract_rejects_invalid_existing_url(self):
        for url in ("postgresql://transcendence_app:pass@wrong-host:5432/transcendence",
                    "postgresql://transcendence_app:pass@postgres:5432/another"):
            with self.subTest(url=url):
                with self.assertRaises(helper.SecretsError):
                    helper.validate_stored("backend", {
                        "data": {"data": {"database_url": url}, "metadata": {"version": 1}}
                    })

    def test_vault_client_does_not_use_proxy(self):
        with patch.object(helper.ssl, "create_default_context") as tls, \
             patch.object(helper, "build_opener") as opener:
            helper.VaultClient("token")
            self.assertTrue(tls.called)
            self.assertTrue(opener.called)
            # Explicit ProxyHandler({}) disables proxies, so token cannot reach a proxy.
            self.assertIsInstance(opener.call_args.args[0], helper.ProxyHandler)
            self.assertEqual(opener.call_args.args[0].proxies, {})
            self.assertIsInstance(opener.call_args.args[2], helper.RejectRedirects)


if __name__ == "__main__":
    unittest.main()
