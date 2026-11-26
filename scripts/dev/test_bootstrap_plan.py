"""Offline plan presentation and command-boundary tests.

Only a metadata-free plan helper runs as a real child. Docker, TLS
commands and all other infrastructure operations are mocked.
"""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

import bootstrap_nginx as nginx
import bootstrap_plan as plan
import bootstrap_plan_events as events
import bootstrap_unified as unified
from bootstrap_progress import Phase, Progress, Status


def completed(code=0, output=b""):
    return subprocess.CompletedProcess([], code, stdout=output)


class PlanProgressTests(unittest.TestCase):
    def test_plan_terminal_states_are_explicitly_scoped(self):
        outcomes = (Status.EXISTS, Status.MISSING, Status.UNVERIFIED,
                    Status.OPTIONAL, Status.READY, Status.NOT_READY)
        labels = tuple("Stage " + str(i) for i in range(len(outcomes)))
        stream = io.StringIO()
        progress = Progress(labels, planning=True, stream=stream, env={"CI": "1"})
        for index, status in enumerate(outcomes):
            progress.begin(index, Phase.CHECK)
            progress.finish(status)
        progress.summary()
        self.assertIn("Finished 6/6 stages", stream.getvalue())
        for status in outcomes[:-1]:
            strict = Progress(("Strict",), stream=io.StringIO(), env={"CI": "1"})
            strict.begin(0, Phase.CHECK)
            with self.assertRaises(ValueError):
                strict.finish(status)

    def run_plan(self, *, host=Status.READY, inventory=Status.EXISTS, overrides=None,
                 failure=None):
        order = []
        overrides = overrides or {}

        def helper(name, session):
            order.append(name)
            if name == failure:
                raise plan.PlanError("Expected helper refusal", 3)
            return overrides.get(name, Status.EXISTS)

        with patch.object(plan, "command", return_value=completed()) as command, \
                patch.object(plan, "host_plan", return_value=host), \
                patch.object(plan, "inventory", return_value=inventory), \
                patch.object(plan, "helper_plan", side_effect=helper), \
                patch.dict(os.environ, {"CI": "1", "FT_DEV_BOOTSTRAP_VERBOSE": "1"}), \
                redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            code = plan.main()
        command.assert_called_once_with([str(plan.ROOT / "scripts/install-deps.sh"), "--check"])
        return code, stdout.getvalue(), stderr.getvalue(), order

    def test_healthy_plan_finishes_all_original_operations_in_order(self):
        code, out, err, order = self.run_plan(overrides={
            "vault_secrets": Status.UNVERIFIED, "grafana_secrets": Status.UNVERIFIED,
            "grafana_https": Status.UNVERIFIED,
        })
        self.assertEqual(code, 0)
        self.assertEqual(order, [name for _, name in plan.OPERATIONS if name is not None])
        self.assertIn("Finished 21/21 stages", out)
        self.assertIn("Vault configuration UNVERIFIED", out)
        self.assertIn("Nginx HTTPS EXISTS", out)
        self.assertNotIn("Nginx HTTPS READY", out)
        self.assertEqual(out.count("RESULT: BOOTSTRAP PLAN COMPLETE (read-only)"), 1)
        self.assertEqual(err, "")
        self.assertNotIn("\x1b", out)

    def test_missing_and_partial_plan_are_normal_results(self):
        code, out, err, order = self.run_plan(host=Status.NOT_READY, inventory=Status.MISSING,
            overrides={"resources": Status.MISSING, "vault": Status.MISSING,
                       "vault_ui": Status.OPTIONAL, "postgres_roles": Status.UNVERIFIED})
        self.assertEqual(code, 0)
        for marker in ("Local host configuration NOT READY", "Vault MISSING",
                       "Optional Vault UI OPTIONAL", "PostgreSQL roles UNVERIFIED"):
            self.assertIn(marker, out)
        self.assertIn("Finished 21/21 stages", out)
        self.assertEqual(err, "")

    def test_plan_refusal_preserves_exit_and_stops_later_operations(self):
        code, out, err, order = self.run_plan(failure="vault")
        self.assertEqual(code, 3)
        self.assertEqual(order, ["resources", "vault"])
        self.assertIn("Vault FAILED", out)
        self.assertIn("Finished 4/21 stages", out)
        self.assertNotIn("BOOTSTRAP PLAN COMPLETE", out)
        self.assertIn("Expected helper refusal", err)

    def test_failed_prerequisites_never_start_inventory(self):
        with patch.object(plan, "command", return_value=completed(1)), \
                patch.object(plan, "host_plan") as host, \
                patch.object(plan, "helper_plan") as helper, \
                redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            self.assertEqual(plan.main(), 1)
        host.assert_not_called()
        helper.assert_not_called()
        self.assertNotIn("BOOTSTRAP PLAN COMPLETE", out.getvalue())

    def test_public_plan_routes_to_shell_and_shell_entry_uses_plan(self):
        with patch.object(unified.os, "geteuid", return_value=1000), \
                patch.object(unified.subprocess, "run", return_value=completed(3)) as run:
            self.assertEqual(unified.main(["plan"]), 3)
        run.assert_called_once_with([str(unified.BOOTSTRAP), "--plan"],
                                    cwd=unified.ROOT, check=False)
        with patch.object(unified.os, "geteuid", return_value=1000), \
                patch.object(plan, "main", return_value=0) as execute, \
                patch.object(unified, "preflight_once") as preflight, \
                patch.object(unified, "exclusive_orchestrator_lock") as lock:
            self.assertEqual(unified.main(["plan", "--shell-entry"], render=True), 0)
        execute.assert_called_once_with(render=True)
        preflight.assert_not_called()
        lock.assert_not_called()

    def test_missing_tls_is_not_fatal_but_partial_tls_is(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(os.environ, {"FT_DEV_TLS_DIR": temp, "XDG_DATA_HOME": temp}), \
                patch.object(plan, "command", return_value=completed()) as command, \
                redirect_stdout(io.StringIO()) as out:
            self.assertIs(plan.host_plan(None), Status.NOT_READY)
            self.assertEqual(command.call_args_list, [
                call([plan.sys.executable, "-m", module, "check"])
                for _, module in plan.HOST_CHECKS])
            (Path(temp) / "ca.crt").write_text("fixture")
            with self.assertRaisesRegex(plan.PlanError, "Incomplete Vault TLS"):
                plan.host_plan(None)
            self.assertEqual(command.call_count, 8)
        self.assertIn("HOST PREPARATION NEEDED", out.getvalue())

    def test_complete_tls_keeps_original_verification_order(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(os.environ, {"FT_DEV_TLS_DIR": temp, "XDG_DATA_HOME": temp}), \
                patch.object(plan, "command", return_value=completed()) as command, \
                redirect_stdout(io.StringIO()):
            tls = Path(temp)
            ca = tls / "ft-transcendence/vault-ca"
            ca.mkdir(parents=True)
            for path in (tls / "ca.crt", tls / "vault.crt", tls / "vault.key", ca / "ca.key"):
                path.write_text("fixture")
            self.assertIs(plan.host_plan(None), Status.READY)
            self.assertEqual(command.call_args_list[-2:], [
                call(["openssl", "verify", "-purpose", "sslserver", "-verify_hostname", "vault",
                      "-CAfile", str(tls / "ca.crt"), str(tls / "vault.crt")]),
                call(["openssl", "x509", "-in", str(tls / "vault.crt"), "-noout", "-checkend", "0"])])
            self.assertEqual(command.call_count, 6)

    def test_inventory_uses_original_names_and_does_not_expose_ids(self):
        def command(args, *, output=False):
            if args[1] == "ps":
                return completed(output=b"PRIVATE-DOCKER-ID\n")
            return completed(1 if args[-1] == plan.VOLUMES[0] else 0)
        with patch.object(plan, "command", side_effect=command) as execute, \
                redirect_stdout(io.StringIO()) as out:
            self.assertIs(plan.inventory(None), Status.UNVERIFIED)
        self.assertEqual(execute.call_count, 17)
        self.assertEqual(execute.call_args_list[:12],
                         [call(["docker", "volume", "inspect", n]) for n in plan.VOLUMES])
        self.assertEqual(execute.call_args_list[12:15],
                         [call(["docker", "network", "inspect", n]) for n in plan.NETWORKS])
        self.assertNotIn("PRIVATE-DOCKER-ID", out.getvalue())

    def test_container_observation_does_not_claim_readiness_or_echo_raw_metadata(self):
        sink = io.StringIO()
        with patch.object(events, "_sink", sink):
            events.container("Nginx", {"State": {"Status": "running",
                "Health": {"Status": "healthy", "Log": "SECRET_SENTINEL"}}})
            events.container("Optional gateway", None, optional=True)
            events.container("Other", {"State": {"Status": "SECRET_SENTINEL"}})
        rows = [plan.decode_observation(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(rows[0], ("Nginx (running)", Status.EXISTS, True))
        self.assertEqual(rows[1][1:], (Status.UNVERIFIED, False))
        self.assertEqual(rows[2], ("Optional gateway", Status.OPTIONAL, True))
        self.assertNotIn("SECRET_SENTINEL", sink.getvalue())

    def test_nginx_plan_observes_only_after_original_identity_check(self):
        sink = io.StringIO()
        with patch.object(events, "_sink", sink), \
                patch.object(nginx, "container", return_value={"State": {"Status": "running"}}), \
                patch.object(nginx, "verify_identity") as identity, \
                patch.object(nginx, "require_prerequisites") as prerequisites, \
                patch.object(nginx, "verify_routes") as routes, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(nginx.main(["plan"]), 0)
        identity.assert_called_once()
        prerequisites.assert_not_called()
        routes.assert_not_called()
        self.assertEqual(plan.decode_observation(sink.getvalue())[1], Status.EXISTS)

    def test_nginx_untrusted_is_not_an_exists_observation(self):
        sink = io.StringIO()
        with patch.object(events, "_sink", sink), \
                patch.object(nginx, "container", return_value={"State": {"Status": "running"}}), \
                patch.object(nginx, "verify_identity", side_effect=nginx.NginxBootstrapError("untrusted")), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertNotEqual(nginx.main(["plan"]), 0)
        self.assertEqual(sink.getvalue(), "")

    def test_nested_prerequisite_cannot_be_mistaken_for_target_status(self):
        sink = io.StringIO()
        with patch.object(events, "_sink", sink):
            with events.nested():
                events.report("PostgreSQL", "MISSING")
            events.report("Roles not inspectable", "UNVERIFIED")
        rows = [plan.decode_observation(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(rows[0], ("PostgreSQL", Status.MISSING, False))
        self.assertEqual(rows[1], ("Roles not inspectable", Status.UNVERIFIED, True))

    def test_missing_primary_observation_is_refused(self):
        child = MagicMock()
        child.__enter__.return_value = child
        child.stdout = iter([json.dumps(["PostgreSQL", "EXISTS", False])])
        child.wait.return_value = 0
        with patch.object(plan.subprocess, "Popen", return_value=child), \
                redirect_stdout(io.StringIO()), self.assertRaisesRegex(
                    plan.PlanError, "no reviewed observations"):
            plan.helper_plan("postgres_roles", None)

    def test_decoder_rejects_unreviewed_data(self):
        for row in (["\x1b[31m", "READY", True], ["Valid", "INVALID", True],
                    ["Valid", "READY", "true"], ["Valid", "ACTION_REQUIRED", True]):
            with self.subTest(row=row), self.assertRaises(plan.PlanError):
                plan.decode_observation(json.dumps(row))

    def test_child_failure_after_observation_cannot_be_success(self):
        child = MagicMock()
        child.__enter__.return_value = child
        child.stdout = iter([json.dumps(["Existing Nginx", "EXISTS", True])])
        child.wait.return_value = 7
        with patch.object(plan.subprocess, "Popen", return_value=child), \
                redirect_stdout(io.StringIO()), self.assertRaises(plan.PlanError) as error:
            plan.helper_plan("nginx", None)
        self.assertEqual(error.exception.code, 7)

    def test_real_metadata_free_child_emits_only_structured_observation(self):
        # This plan branch just describes secret inspection, never reads Vault.
        with redirect_stdout(io.StringIO()) as out:
            self.assertIs(plan.helper_plan("vault_secrets", None), Status.UNVERIFIED)
        self.assertIn("Authenticated database secret inspection not performed", out.getvalue())
        self.assertNotIn("DATABASE SECRETS PLAN COMPLETE", out.getvalue())

    def test_worker_allowlist_refuses_arbitrary_module(self):
        with patch.object(plan.importlib, "import_module") as imported:
            self.assertEqual(plan.worker("os"), 2)
        imported.assert_not_called()


if __name__ == "__main__":
    unittest.main()
