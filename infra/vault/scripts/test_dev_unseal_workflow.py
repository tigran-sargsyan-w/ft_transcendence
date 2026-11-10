"""Offline, secret-free tests for development-only host-assisted Vault unseal."""

import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("dev_unseal_workflow.py")
spec = importlib.util.spec_from_file_location("dev_unseal_workflow", SCRIPT)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.sealed = {
            "initialized": True, "sealed": True, "type": "shamir",
            "n": 3, "t": 2, "progress": 0,
        }
        self.unsealed = dict(self.sealed, sealed=False, progress=0)
        self.tls = Path("/test/tls")

    def test_already_unsealed_never_reads_keyring(self):
        with (patch.object(w, "_storage_identity", return_value="now"),
              patch.object(w, "_vault_request", return_value=self.unsealed),
              patch.object(w, "_require_login_unlocked") as keyring,
              patch.object(w, "_lookup") as lookup,
              redirect_stdout(io.StringIO())):
            self.assertEqual(w.unseal(self.tls), 0)
        keyring.assert_not_called()
        lookup.assert_not_called()

    def test_threshold_shares_unseal_in_order(self):
        fake_shares = ["fake-share-one-000000", "fake-share-two-000000"]
        responses = [self.sealed, dict(self.sealed, progress=1), self.unsealed, self.unsealed]
        with (patch.object(w, "_storage_identity", return_value="now"),
              patch.object(w, "_require_login_unlocked"),
              patch.object(w, "_lookup", side_effect=fake_shares) as lookup,
              patch.object(w, "_vault_request", side_effect=responses) as api,
              redirect_stdout(io.StringIO())):
            self.assertEqual(w.unseal(self.tls), 0)
        self.assertEqual(lookup.call_count, 2)
        self.assertEqual([c.args[0] for c in api.call_args_list],
                         ["status", "unseal", "unseal", "status"])
        self.assertEqual(api.call_args_list[1].args[2], fake_shares[0])
        self.assertEqual(api.call_args_list[2].args[2], fake_shares[1])

    def test_partial_progress_never_reads_shares(self):
        with (patch.object(w, "_storage_identity", return_value="now"),
              patch.object(w, "_vault_request", return_value=dict(self.sealed, progress=1)),
              patch.object(w, "_lookup") as lookup):
            with self.assertRaises(w.WorkflowError):
                w.unseal(self.tls)
        lookup.assert_not_called()

    def test_missing_share_stops_before_submitting(self):
        with (patch.object(w, "_storage_identity", return_value="now"),
              patch.object(w, "_vault_request", return_value=self.sealed) as api,
              patch.object(w, "_require_login_unlocked"),
              patch.object(w, "_lookup", side_effect=["fake-share-000000", None])):
            with self.assertRaises(w.WorkflowError):
                w.unseal(self.tls)
        self.assertEqual(api.call_count, 1)

    def test_duplicate_shares_rejected(self):
        with (patch.object(w, "_storage_identity", return_value="now"),
              patch.object(w, "_vault_request", return_value=self.sealed) as api,
              patch.object(w, "_require_login_unlocked"),
              patch.object(w, "_lookup", return_value="fake-share-000000")):
            with self.assertRaises(w.WorkflowError):
                w.unseal(self.tls)
        self.assertEqual(api.call_count, 1)

    def test_unexpected_threshold_rejected(self):
        with (patch.object(w, "_storage_identity", return_value="now"),
              patch.object(w, "_vault_request", return_value=dict(self.sealed, t=3)),
              patch.object(w, "_lookup") as lookup):
            with self.assertRaises(w.WorkflowError):
                w.unseal(self.tls)
        lookup.assert_not_called()

    def test_uninitialized_vault_rejected(self):
        with (patch.object(w, "_storage_identity", return_value="now"),
              patch.object(w, "_vault_request", return_value=dict(self.sealed, initialized=False))):
            with self.assertRaises(w.WorkflowError):
                w.unseal(self.tls)

    def test_enroll_requires_unsealed(self):
        with (patch.object(w, "_storage_identity", return_value="now"),
              patch.object(w, "_require_login_unlocked"),
              patch.object(w, "_vault_request", return_value=self.sealed),
              patch.object(w, "_store") as store):
            with self.assertRaises(w.WorkflowError):
                w.enroll(self.tls)
        store.assert_not_called()

    def test_enroll_two_distinct_keys(self):
        fake_shares = ["fake-share-111111111", "fake-share-111111111",
                       "fake-share-222222222", "fake-share-222222222"]
        with (patch.object(w, "_storage_identity", return_value="now"),
              patch.object(w, "_require_login_unlocked"),
              patch.object(w, "_vault_request", return_value=self.unsealed),
              patch.object(w, "_lookup", return_value=None),
              patch.object(w, "_store") as store,
              patch.object(w.getpass, "getpass", side_effect=fake_shares),
              patch("builtins.input", return_value="ENROLL"),
              redirect_stdout(io.StringIO()) as out):
            self.assertEqual(w.enroll(self.tls), 0)
        self.assertEqual(store.call_count, 2)
        self.assertEqual(store.call_args_list[0].args[2], fake_shares[0])
        self.assertEqual(store.call_args_list[1].args[2], fake_shares[2])
        self.assertFalse(any(s in out.getvalue() for s in fake_shares))

    def test_enroll_second_store_failure_rolls_back(self):
        with (patch.object(w, "_storage_identity", return_value="now"),
              patch.object(w, "_require_login_unlocked"),
              patch.object(w, "_vault_request", return_value=self.unsealed),
              patch.object(w, "_lookup", return_value=None),
              patch.object(w, "_store", side_effect=[None, w.WorkflowError("store failed")]),
              patch.object(w, "_clear_all") as cleared,
              patch.object(w.getpass, "getpass", side_effect=[
                  "fake-share-111111111", "fake-share-111111111",
                  "fake-share-222222222", "fake-share-222222222",
              ]),
              patch("builtins.input", return_value="ENROLL")):
            with self.assertRaises(w.WorkflowError):
                w.enroll(self.tls)
        cleared.assert_called_once()

    def test_forget_confirmation_required(self):
        with (patch.object(w, "_require_login_unlocked"),
              patch("builtins.input", return_value="no"),
              patch.object(w, "_clear_all") as clear):
            with self.assertRaises(w.WorkflowError):
                w.forget()
        clear.assert_not_called()

    def test_forget_explicit_confirmation(self):
        with (patch.object(w, "_require_login_unlocked"),
              patch("builtins.input", return_value="FORGET"),
              patch.object(w, "_clear_all") as clear,
              redirect_stdout(io.StringIO())):
            self.assertEqual(w.forget(), 0)
        clear.assert_called_once()

    def test_network_error_returns_sanitized_message(self):
        failure = subprocess.CompletedProcess(args=[], returncode=125,
                                              stdout="secret-value", stderr="secret-value")
        with (patch.object(w, "_json_docker", return_value=[{
                  "Name": w.VAULT_NETWORK,
                  "Labels": {"com.docker.compose.project": w.VAULT_PROJECT},
              }]),
              patch.object(w, "_call", side_effect=[
                  subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
                  failure,
              ]),
              patch.object(Path, "is_file", return_value=True),
              patch.object(Path, "is_symlink", return_value=False)):
            with self.assertRaises(w.WorkflowError) as caught:
                w._vault_request("status", self.tls)
        self.assertNotIn("secret-value", str(caught.exception))

    def test_https_client_passes_share_only_in_stdin(self):
        share = "fake-secret-999999999"
        captured = []
        def fake_call(args, **kwargs):
            captured.append((args, kwargs))
            if args[:3] == ["docker", "image", "inspect"]:
                return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(args=[], returncode=0,
                stdout=json.dumps(self.unsealed), stderr="")
        with (patch.object(w, "_json_docker", return_value=[{
                  "Name": w.VAULT_NETWORK,
                  "Labels": {"com.docker.compose.project": w.VAULT_PROJECT},
              }]),
              patch.object(w, "_call", side_effect=fake_call),
              patch.object(Path, "is_file", return_value=True),
              patch.object(Path, "is_symlink", return_value=False)):
            self.assertFalse(w._vault_request("unseal", self.tls, share)["sealed"])
        args, options = captured[-1]
        self.assertNotIn(share, " ".join(args))
        self.assertEqual(json.loads(options["data"])["key"], share)
        self.assertIn("--read-only", args)
        self.assertIn("--cap-drop=ALL", args)

    def test_run_command_suppresses_sensitive_error(self):
        with (patch.object(w, "_storage_identity", side_effect=w.WorkflowError("unsafe")),
              redirect_stdout(io.StringIO()) as out):
            self.assertEqual(w.run_command("unseal", self.tls), 1)
        self.assertNotIn("unsafe", out.getvalue())


if __name__ == "__main__":
    unittest.main()
