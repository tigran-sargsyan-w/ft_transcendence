"""Phase 6D acceptance tests: entirely offline; Docker is always mocked."""

import contextlib
import copy
import io
import types
import unittest
from unittest.mock import call, patch

import verify_startup_idempotency as acceptance


def fixture():
    return {
        "container_inventory": tuple(sorted(acceptance.CONTAINERS)),
        "volume_inventory": tuple(sorted(acceptance.VOLUMES)),
        "network_inventory": tuple(sorted(acceptance.NETWORKS)),
        "migration_history": "a" * 32,
        "containers": {name: ("id:" + name, "creation", 0, "start") for name in acceptance.CONTAINERS},
        "volumes": {name: ("creation", "local", "/mount/" + name) for name in acceptance.VOLUMES},
        "networks": {name: ("id:" + name, "creation") for name in acceptance.NETWORKS},
    }


class IdempotencyAcceptanceTests(unittest.TestCase):
    def test_expected_resource_inventory(self):
        self.assertEqual(len(acceptance.CONTAINERS), 9)
        self.assertEqual(len(acceptance.VOLUMES), 8)
        self.assertEqual(len(acceptance.NETWORKS), 3)

    def test_healthy_repeated_runs_no_mutation(self):
        with patch.object(acceptance, "trusted") as trusted, \
             patch.object(acceptance, "snapshot", return_value=fixture()) as snapshot, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            acceptance.verify(runs=3)
        self.assertEqual(snapshot.call_count, 7)
        self.assertEqual(trusted.call_args_list, [call("--health-report")] +
                         [c for _ in range(3) for c in (call("--start"), call("--health-report"))])
        self.assertIn("RESULT: STARTUP IDEMPOTENCY VERIFIED", output.getvalue())

    def test_check_only_cannot_start(self):
        with patch.object(acceptance, "trusted") as trusted, \
             patch.object(acceptance, "snapshot", return_value=fixture()) as snap, \
             contextlib.redirect_stdout(io.StringIO()):
            acceptance.verify(read_only=True)
        trusted.assert_called_once_with("--health-report")
        snap.assert_called_once()

    def test_failing_preflight_does_not_start_or_snapshot(self):
        with patch.object(acceptance, "trusted", side_effect=acceptance.AcceptanceError("bad")) as trusted, \
             patch.object(acceptance, "snapshot") as snap:
            with self.assertRaises(acceptance.AcceptanceError):
                acceptance.verify()
        trusted.assert_called_once_with("--health-report")
        snap.assert_not_called()

    def test_first_start_failure_stops_before_snapshot(self):
        def fail_start(flag):
            if flag == "--start":
                raise acceptance.AcceptanceError("failed")
        with patch.object(acceptance, "trusted", side_effect=fail_start) as trusted, \
             patch.object(acceptance, "snapshot", return_value=fixture()) as snap, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(acceptance.AcceptanceError):
                acceptance.verify()
        self.assertEqual(snap.call_count, 1)
        self.assertEqual(trusted.call_count, 2)

    def test_recreated_container_fails_without_rollback(self):
        changed = fixture()
        changed["containers"][acceptance.CONTAINERS[0]] = ("different-id", "creation", 0, "start")
        with patch.object(acceptance, "snapshot", side_effect=[fixture(), changed]) as snap, \
             patch.object(acceptance, "trusted") as trusted, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(acceptance.AcceptanceError, "lifecycle change"):
                acceptance.verify(runs=3)
        self.assertEqual(snap.call_count, 2)
        self.assertEqual(trusted.call_args_list, [call("--health-report"), call("--start")])

    def test_restarted_container_fails(self):
        changed = fixture()
        name = acceptance.CONTAINERS[4]
        original = changed["containers"][name]
        changed["containers"][name] = (original[0], original[1], 1, "new start")
        with self.assertRaisesRegex(acceptance.AcceptanceError, name):
            acceptance.compare(fixture(), changed)

    def test_unexpected_new_volume_fails(self):
        changed = fixture()
        changed["volume_inventory"] += ("transcendence-app-dev_unexpected",)
        with self.assertRaisesRegex(acceptance.AcceptanceError, "volume_inventory"):
            acceptance.compare(fixture(), changed)

    def test_network_recreation_fails(self):
        changed = fixture()
        name = acceptance.NETWORKS[0]
        changed["networks"][name] = ("different-id", "creation")
        with self.assertRaisesRegex(acceptance.AcceptanceError, "networks lifecycle"):
            acceptance.compare(fixture(), changed)

    def test_migration_history_change_fails(self):
        changed = fixture()
        changed["migration_history"] = "b" * 32
        with self.assertRaisesRegex(acceptance.AcceptanceError, "migration_history"):
            acceptance.compare(fixture(), changed)

    def test_inspection_does_not_dump_docker_json(self):
        sensitive = [{"Name": "/" + acceptance.CONTAINERS[0], "Config": {"Env": ["TOKEN=never-print"]}}]
        with patch.object(acceptance, "docker_json", return_value=sensitive), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(acceptance.AcceptanceError):
                acceptance.inspected("container", acceptance.CONTAINERS)
        self.assertNotIn("never-print", output.getvalue())

    def test_inspected_rejects_wrong_name(self):
        with patch.object(acceptance, "docker_json", return_value=[{"Name": "/other"}]):
            with self.assertRaises(acceptance.AcceptanceError):
                acceptance.inspected("container", (acceptance.CONTAINERS[0],))

    def test_invalid_fingerprint_rejected(self):
        with patch.object(acceptance, "call", return_value=b"password_abc\n"):
            with self.assertRaisesRegex(acceptance.AcceptanceError, "metadata fingerprint"):
                acceptance.migration_fingerprint()

    def test_valid_fingerprint_returned_without_logs(self):
        with patch.object(acceptance, "call", return_value=("c" * 32 + "\n").encode()), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(acceptance.migration_fingerprint(), "c" * 32)
        self.assertEqual(output.getvalue(), "")

    def test_trusted_allowlist(self):
        with self.assertRaisesRegex(acceptance.AcceptanceError, "Disallowed"):
            acceptance.trusted("--initialize")

    def test_trusted_does_not_use_shell_or_expose_output(self):
        with patch.object(acceptance.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as runner:
            acceptance.trusted("--start")
        self.assertEqual(runner.call_args.args[0], [str(acceptance.BOOTSTRAP), "--start"])
        self.assertEqual(runner.call_args.kwargs["stdout"], acceptance.subprocess.DEVNULL)
        self.assertEqual(runner.call_args.kwargs["stderr"], acceptance.subprocess.DEVNULL)
        self.assertNotIn("shell", runner.call_args.kwargs)

    def test_invalid_runs_never_accesses_system(self):
        with patch.object(acceptance, "trusted") as trusted:
            for count in (0, 6, -3):
                with self.assertRaisesRegex(acceptance.AcceptanceError, "between"):
                    acceptance.verify(runs=count)
        trusted.assert_not_called()

    def test_main_refuses_root(self):
        with patch.object(acceptance.os, "geteuid", return_value=0), \
             patch.object(acceptance, "verify") as verified, \
             contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(acceptance.main(["--runs", "2"]), 1)
        verified.assert_not_called()
        self.assertIn("ordinary developer", err.getvalue())

    def test_main_check_only(self):
        with patch.object(acceptance.os, "geteuid", return_value=1000), \
             patch.object(acceptance, "verify") as verified:
            self.assertEqual(acceptance.main(["--check-only"]), 0)
        verified.assert_called_once_with(runs=3, read_only=True)


    def test_snapshot_collects_only_safe_fields(self):
        containers = [{
            "Name": "/" + name, "Id": "id:" + name,
            "Created": "creation", "RestartCount": 0,
            "State": {"Running": True, "Status": "running", "StartedAt": "start"},
            "Config": {"Env": ["HIDDEN=should-not-be-persisted"]},
        } for name in acceptance.CONTAINERS]
        volumes = [{
            "Name": name, "CreatedAt": "creation", "Driver": "local",
            "Mountpoint": "/mount/" + name,
        } for name in acceptance.VOLUMES]
        networks = [{"Name": name, "Id": "id:" + name, "Created": "creation"}
                    for name in acceptance.NETWORKS]
        def fake_json(*args):
            return {"container": containers, "volume": volumes, "network": networks}[args[0]]
        with patch.object(acceptance, "docker_json", side_effect=fake_json), \
             patch.object(acceptance, "names_for", side_effect=lambda kind: fixture()[kind + "_inventory"]), \
             patch.object(acceptance, "migration_fingerprint", return_value="a" * 32):
            state = acceptance.snapshot()
        self.assertEqual(state, fixture())
        self.assertNotIn("should-not-be-persisted", repr(state))

    def test_snapshot_refuses_stopped_container(self):
        containers = [{"Name": "/" + name, "Id": "id:" + name,
                       "State": {"Running": name != acceptance.CONTAINERS[2],
                                 "Status": "running" if name != acceptance.CONTAINERS[2] else "exited"}}
                      for name in acceptance.CONTAINERS]
        volumes = [{"Name": name} for name in acceptance.VOLUMES]
        networks = [{"Name": name} for name in acceptance.NETWORKS]
        with patch.object(acceptance, "docker_json", side_effect=lambda *args: {
            "container": containers, "volume": volumes, "network": networks
        }[args[0]]), patch.object(acceptance, "names_for", return_value=()), \
             patch.object(acceptance, "migration_fingerprint", return_value="a" * 32):
            with self.assertRaisesRegex(acceptance.AcceptanceError, "not running"):
                acceptance.snapshot()

    def test_names_for_rejects_unrelated_host_resources(self):
        with patch.object(acceptance, "call", return_value=(
            b"foreign-container\ntranscendence-app-dev-backend-1\n"
            b"transcendence-vault-dev-vault-1\n")) as runner:
            self.assertEqual(acceptance.names_for("container"), (
                "transcendence-app-dev-backend-1", "transcendence-vault-dev-vault-1"))
        self.assertEqual(runner.call_args.args[0][:4],
                         ["docker", "container", "ls", "-a"])

    def test_docker_command_errors_suppress_unsafe_output(self):
        with patch.object(acceptance.subprocess, "run",
                          return_value=types.SimpleNamespace(returncode=1, stdout=b"SECRET")):
            with self.assertRaises(acceptance.AcceptanceError) as exc:
                acceptance.call(["docker", "container", "inspect", "anything"])
        self.assertNotIn("SECRET", str(exc.exception))

    def test_snapshot_incomplete_inspect_rejected(self):
        with patch.object(acceptance, "docker_json", return_value=[]):
            with self.assertRaisesRegex(acceptance.AcceptanceError, "Missing container"):
                acceptance.inspected("container", acceptance.CONTAINERS)


if __name__ == "__main__":
    unittest.main()
