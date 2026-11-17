"""Offline safety tests for batched Docker resource metadata inspection."""
import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "bootstrap_resources", Path(__file__).with_name("bootstrap_resources.py")
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class BatchResourceInspectionTests(unittest.TestCase):
    @staticmethod
    def fixture(kind):
        if kind == "network":
            return [{
                "Name": name, "Driver": "bridge", "Internal": private,
                "Labels": {
                    "com.docker.compose.project": project,
                    "com.docker.compose.network": internal,
                },
            } for name, (project, internal, private) in mod.NETWORKS.items()]
        return [{
            "Name": name, "Driver": "local",
            "Labels": {
                "com.docker.compose.project": project,
                "com.docker.compose.volume": internal,
            },
        } for name, (project, internal) in mod.VOLUMES.items()]

    def test_batched_healthy_inventory_two_inspect_calls(self):
        def fake_command(args, **kwargs):
            self.assertEqual(args[0], "docker")
            self.assertEqual(args[2], "inspect")
            manifest = mod.NETWORKS if args[1] == "network" else mod.VOLUMES
            self.assertEqual(args[3:], list(manifest))
            self.assertEqual(kwargs["timeout"], mod.INSPECT_TIMEOUT)
            return json.dumps(self.fixture(args[1])).encode()

        with patch.object(mod, "command", side_effect=fake_command) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            state = mod.scan(batch=True)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(len(state), 11)
        self.assertTrue(all(state.values()))

    def test_batch_rejects_untrusted_manifest_before_docker_call(self):
        with patch.object(mod, "command") as run:
            with self.assertRaisesRegex(mod.ResourceError, "Unexpected"):
                mod.inspect_many("network", {"untrusted": None})
            with self.assertRaisesRegex(mod.ResourceError, "Unexpected"):
                mod.inspect_many("container", mod.NETWORKS)
        run.assert_not_called()

    def test_batch_rejects_duplicate_names_without_fallback(self):
        records = self.fixture("network")
        records[-1] = records[0].copy()
        with patch.object(mod, "command", return_value=json.dumps(records).encode()) as run, \
                patch.object(mod, "inspect") as single:
            with self.assertRaisesRegex(mod.ResourceError, "repeated"):
                mod.inspect_many("network", mod.NETWORKS)
        run.assert_called_once()
        single.assert_not_called()

    def test_batch_rejects_malformed_names(self):
        records = self.fixture("network")
        records[0]["Name"] = ["unexpected"]
        with patch.object(mod, "command", return_value=json.dumps(records).encode()):
            with self.assertRaisesRegex(mod.ResourceError, "Unexpected or repeated"):
                mod.inspect_many("network", mod.NETWORKS)

    def test_batch_rejects_unsafe_labels(self):
        def fake_command(args, **kwargs):
            data = self.fixture(args[1])
            if args[1] == "network":
                data[0]["Labels"]["com.docker.compose.project"] = "untrusted"
            return json.dumps(data).encode()

        with patch.object(mod, "command", side_effect=fake_command), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(mod.ResourceError, "incompatible existing network"):
                mod.scan(batch=True)

    def test_batch_nonzero_falls_back_to_single_checks(self):
        absent = next(iter(mod.VOLUMES))
        with patch.object(mod, "command", side_effect=mod.ResourceError("missing")) as batch, \
                patch.object(mod, "inspect", side_effect=lambda kind, name:
                             None if name == absent else {"Name": name}) as single:
            state = mod.inspect_many("volume", mod.VOLUMES)
        batch.assert_called_once()
        self.assertEqual(single.call_count, len(mod.VOLUMES))
        self.assertIsNone(state[absent])
        self.assertEqual(set(state), set(mod.VOLUMES))

    def test_batch_retries_only_timeouts_then_refuses(self):
        with patch.object(mod, "command", side_effect=mod.ResourceInspectTimeout("slow")) as run, \
                patch.object(mod.time, "sleep") as sleep, \
                patch.object(mod, "inspect") as single:
            with self.assertRaisesRegex(mod.ResourceError, "batch inspection repeatedly timed out"):
                mod.inspect_many("network", mod.NETWORKS)
        self.assertEqual(run.call_count, mod.INSPECT_ATTEMPTS)
        self.assertEqual(sleep.call_count, mod.INSPECT_ATTEMPTS - 1)
        single.assert_not_called()

    def test_main_only_batches_explicit_read_only_check(self):
        with patch.object(mod, "validate_compose_contract"), \
                patch.object(mod, "scan", return_value={"ok": True}) as scan, \
                patch.object(sys, "argv", ["bootstrap_resources.py", "check"]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mod.main(), 0)
        scan.assert_called_once_with(batch=True)


if __name__ == "__main__":
    unittest.main()
