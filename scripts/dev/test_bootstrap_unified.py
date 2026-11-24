"""Phase 6A orchestration tests: offline, no Docker or host modifications."""

import contextlib
import io
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import bootstrap_unified as unified


class UnifiedTests(unittest.TestCase):
    def test_fixed_stage_order(self):
        self.assertEqual([s[0] for s in unified.STAGES], [
            "host", "resources", "vault", "vault-ui", "vault-config", "vault-secrets",
            "postgres", "postgres-roles", "migrations", "backend-agent",
            "backend", "support-services", "frontend", "nginx",
            "prometheus", "grafana-secrets", "grafana-agent",
            "grafana", "grafana-https",
        ])

    def test_unknown_command_denied(self):
        with self.assertRaises(unified.UnifiedStartupError):
            unified.command("--delete-volumes")

    def test_command_check_is_quiet(self):
        with patch.object(unified.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as run:
            self.assertTrue(unified.command("--check-nginx", quiet=True))
        self.assertEqual(run.call_args.args[0], [
            unified.sys.executable,
            str(unified.ROOT / "scripts/dev/bootstrap_nginx.py"),
            "check",
        ])
        self.assertIn("FT_DEV_TLS_DIR", run.call_args.kwargs["env"])
        self.assertEqual(run.call_args.kwargs["stdout"], unified.subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["stderr"], unified.subprocess.DEVNULL)

    def test_command_failed_check(self):
        with patch.object(unified.subprocess, "run", return_value=types.SimpleNamespace(returncode=1)):
            self.assertFalse(unified.command("--check-nginx", quiet=True))

    def test_check_all_is_read_only(self):
        with patch.object(unified, "command", return_value=True) as cmd, contextlib.redirect_stdout(io.StringIO()):
            unified.check()
        self.assertEqual([call.args[0] for call in cmd.call_args_list],
                         ["--check-host", "--check-resources", "--check-vault-ui-optional", "--check-nginx", "--check-prometheus", "--check-grafana", "--check-grafana-https"])
        self.assertTrue(all(call.kwargs["quiet"] for call in cmd.call_args_list))

    def test_check_stops_on_first_failure(self):
        with patch.object(unified, "command", return_value=False) as cmd, \
             patch.object(unified.time, "sleep") as sleep:
            with self.assertRaises(unified.UnifiedStartupError):
                unified.check()
        self.assertEqual(cmd.call_count, unified.READ_ONLY_ATTEMPTS)
        self.assertEqual(sleep.call_count, unified.READ_ONLY_ATTEMPTS - 1)

    def test_health_report_fast_path_checks_prometheus(self):
        with patch.object(unified, "command", return_value=True) as command, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            unified.health_report()
        self.assertEqual([call.args[0] for call in command.call_args_list],
                         ["--check-host", "--check-resources", "--check-nginx", "--check-vault-ui-optional", "--check-prometheus", "--check-grafana", "--check-grafana-https"])
        self.assertTrue(all(call.kwargs["quiet"] for call in command.call_args_list))
        self.assertIn("RESULT: DEVELOPMENT HEALTHY", output.getvalue())

    def test_health_report_localizes_first_failing_dependency(self):
        checks = {"nginx": False, "postgres": False}
        with patch.object(unified, "checked", side_effect=lambda name: checks.get(name, True)) as ck, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "postgres"):
                unified.health_report()
        self.assertEqual([c.args[0] for c in ck.call_args_list],
                         ["host", "resources", "nginx", "vault", "vault-ui", "vault-config", "vault-secrets", "postgres"])
        self.assertIn("FAIL: postgres; inspect --check-postgres", output.getvalue())
        self.assertNotIn("RESULT: DEVELOPMENT HEALTHY", output.getvalue())

    def test_health_report_rejects_intermittent_instead_of_false_healthy(self):
        vals = [True, True, False] + [True] * (len(unified.STAGES) - 2)
        with patch.object(unified, "checked", side_effect=vals) as ck, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "Intermittent"):
                unified.health_report()
        self.assertEqual(ck.call_count, len(unified.STAGES) + 1)
        self.assertIn("WARN: end-to-end check failed", output.getvalue())

    def test_health_report_host_failure_never_probes_docker(self):
        with patch.object(unified, "checked", return_value=False) as ck, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "host"):
                unified.health_report()
        self.assertEqual([c.args[0] for c in ck.call_args_list], ["host"])
        self.assertIn("FAIL: host; inspect --check-host", output.getvalue())

    def test_health_report_resources_failure_stops(self):
        with patch.object(unified, "checked", side_effect=[True, False]) as ck, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "resources"):
                unified.health_report()
        self.assertEqual([c.args[0] for c in ck.call_args_list], ["host", "resources"])
        self.assertIn("FAIL: resources; inspect --check-resources", output.getvalue())

    def test_health_report_main_read_only_no_lock_or_mutation(self):
        with patch.object(unified.os, "geteuid", return_value=1000), \
             patch.object(unified, "health_report") as report, \
             patch.object(unified, "preflight_once") as preflight, \
             patch.object(unified, "exclusive_orchestrator_lock") as lock:
            self.assertEqual(unified.main(["health-report"]), 0)
        report.assert_called_once()
        preflight.assert_called_once_with()
        lock.assert_not_called()

    def fake_prerequisites(self, runner, progress):
        for index in range(2):
            progress.begin(index)
            progress.finish(unified.Status.DONE)

    def test_ready_prerequisites_are_cached_without_privileged_install(self):
        runner = MagicMock()
        runner.run.return_value = 0
        progress = MagicMock()
        unified.prepare_prerequisites(runner, progress)
        self.assertEqual([call.args[0][-1] for call in runner.run.call_args_list],
                         ["--initialize-check", "--orchestrator-preflight", "preflight", "check"])
        self.assertEqual(progress.finish.call_args_list,
                         [call(unified.Status.CACHED), call(unified.Status.CACHED)])

    def test_session_refresh_does_not_continue_to_images(self):
        runner = MagicMock()
        runner.run.side_effect = [1, 0, 2]
        runner.last_diagnostics.session_refresh = True
        with self.assertRaises(unified.SessionRefreshRequired):
            unified.prepare_prerequisites(runner, MagicMock())
        self.assertEqual(runner.run.call_count, 3)
        self.assertEqual(runner.run.call_args_list[1].args[0], ["sudo", "-v"])
        self.assertFalse(runner.run.call_args_list[2].kwargs["pause_for_auth"])

    def test_local_prerequisite_guard_failure_does_not_authenticate(self):
        runner = MagicMock()
        runner.run.return_value = 3
        with self.assertRaises(unified.UnifiedStartupError):
            unified.prepare_prerequisites(runner, MagicMock())
        runner.run.assert_called_once()

    def test_initialization_all_ready_without_tty_has_no_apply(self):
        with patch.object(unified.sys.stdin, "isatty", return_value=False), \
             patch.object(unified, "prepare_prerequisites", side_effect=self.fake_prerequisites), \
             patch.object(unified, "command", return_value=True) as cmd, \
             contextlib.redirect_stdout(io.StringIO()):
            unified.initialize(9)
        self.assertEqual([call.args[0] for call in cmd.call_args_list],
                         [stage[1] for stage in unified.STAGES if stage[0] != "vault-ui"])

    def test_initialization_prepares_only_missing_stage(self):
        responses = [False] * unified.READ_ONLY_ATTEMPTS + [True, True] + [True] * (len(unified.STAGES) - 1)
        with patch.object(unified, "prepare_prerequisites", side_effect=self.fake_prerequisites), \
             patch.object(unified, "command", side_effect=responses) as cmd, \
             patch.object(unified.time, "sleep"), \
             contextlib.redirect_stdout(io.StringIO()):
            unified.initialize(9)
        self.assertEqual([call.args[0] for call in cmd.call_args_list][:5],
                         ["--check-host"] * unified.READ_ONLY_ATTEMPTS + ["--prepare-host", "--check-host"])
        self.assertEqual(cmd.call_args_list[3].kwargs["lock_fd"], 9)
        self.assertNotIn("--prepare-vault", [call.args[0] for call in cmd.call_args_list])

    def test_initialization_halts_on_failed_prepare(self):
        with patch.object(unified, "prepare_prerequisites", side_effect=self.fake_prerequisites), \
             patch.object(unified, "command", side_effect=[False] * (unified.READ_ONLY_ATTEMPTS + 1)) as cmd, \
             patch.object(unified.time, "sleep"):
            with self.assertRaises(unified.UnifiedStartupError):
                unified.initialize(9)
        self.assertEqual(cmd.call_count, unified.READ_ONLY_ATTEMPTS + 1)

    def test_initialize_dispatch_passes_lock_only_for_prepare(self):
        runner = MagicMock()
        runner.run.return_value = 0
        self.assertTrue(unified.command("--prepare-vault", runner=runner, lock_fd=9))
        self.assertEqual(runner.run.call_args.args[0],
                         [str(unified.BOOTSTRAP), "--initialize-stage", "--prepare-vault", "9"])
        self.assertEqual(runner.run.call_args.kwargs["pass_fds"], (9,))
        self.assertTrue(runner.run.call_args.kwargs["critical"])
        self.assertTrue(unified.command("--check-vault", runner=runner))
        self.assertEqual(runner.run.call_args.kwargs["pass_fds"], ())
        self.assertFalse(runner.run.call_args.kwargs["critical"])
        with self.assertRaises(unified.UnifiedStartupError):
            unified.command("--prepare-host", runner=runner)

    def test_unexpected_prompt_never_triggers_preparation(self):
        with patch.object(unified, "prepare_prerequisites", side_effect=self.fake_prerequisites), \
             patch.object(unified, "command", side_effect=unified.UnexpectedApproval) as cmd, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(unified.UnexpectedApproval):
                unified.initialize(9)
        self.assertEqual(cmd.call_count, 1)
        self.assertIn("host FAILED", output.getvalue())

    def test_routine_does_not_run_any_prepare_flag(self):
        with patch.object(unified, "checked", return_value=True) as ck, \
             patch.object(unified, "vault_seal_guard") as vault, \
             contextlib.redirect_stdout(io.StringIO()):
            unified.start()
        self.assertEqual([call.args[0] for call in ck.call_args_list],
                         ["host", "resources", "vault-ui", "nginx", "prometheus", "grafana", "grafana-https"])
        vault.assert_called_once()

    def test_routine_fails_before_database_if_vault_sealed(self):
        with patch.object(unified, "checked", return_value=True) as ck, \
             patch.object(unified, "vault_seal_guard", side_effect=unified.UnifiedStartupError("sealed")):
            with self.assertRaisesRegex(unified.UnifiedStartupError, "sealed"):
                unified.start()
        self.assertEqual([call.args[0] for call in ck.call_args_list], ["host", "resources"])

    def test_routine_rejects_incomplete_migrations_without_apply(self):
        checks = lambda name: name not in ("nginx", "migrations")
        with patch.object(unified, "checked", side_effect=checks), \
             patch.object(unified, "vault_seal_guard"), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(unified.UnifiedStartupError, "migrations"):
                unified.start()

    def test_reboot_with_enabled_but_stopped_ui_does_not_block_core_start(self):
        # Simulates a persisted enabled marker and restart:no HAProxy after VM reboot.
        # The core stack must still reach READY without starting the gateway.
        with (
            patch.object(unified, "checked", side_effect=lambda name, **_: name != "vault-ui") as probe,
            patch.object(unified, "vault_seal_guard"),
            patch.object(unified, "resume") as resume,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            unified.start()
        resume.assert_not_called()
        self.assertIn("WARN: optional Vault UI state unhealthy", output.getvalue())
        self.assertIn("RESULT: UNIFIED STARTUP READY", output.getvalue())
        self.assertEqual([call.args[0] for call in probe.call_args_list],
                         ["host", "resources", "vault-ui", "nginx",
                          "prometheus", "grafana", "grafana-https"])

    def test_initialize_never_requires_optional_gateway(self):
        with (
            patch.object(unified, "prepare_prerequisites", side_effect=self.fake_prerequisites),
            patch.object(unified, "checked", side_effect=lambda name, **_: name != "vault-ui") as probe,
            patch.object(unified, "command") as command,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            unified.initialize(9)
        command.assert_not_called()
        self.assertNotIn("vault-ui", [call.args[0] for call in probe.call_args_list])
        self.assertIn("vault-ui SKIPPED", output.getvalue())

    def test_health_report_rejects_missing_enabled_gateway(self):
        with (
            patch.object(unified, "checked", side_effect=lambda name: name != "vault-ui"),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            with self.assertRaisesRegex(unified.UnifiedStartupError, "vault-ui"):
                unified.health_report()
        self.assertIn("FAIL: vault-ui", output.getvalue())
        self.assertNotIn("RESULT: DEVELOPMENT HEALTHY", output.getvalue())

    def test_guard_resume_vault_ui_verifies_identity(self):
        ui = types.ModuleType("bootstrap_vault_ui")
        ui.NAME = "trusted-vault-ui"

        item = {
            "Id": "original-gateway-id",
            "State": {
                "Running": False,
                "Status": "exited",
            },
        }

        ui.inspect = MagicMock(return_value=item)
        ui.verify_identity = MagicMock()

        with patch.dict("sys.modules", {"bootstrap_vault_ui": ui}):
            targets = unified.guard_resume("vault-ui")

        self.assertEqual(len(targets), 1)
        self.assertEqual(
            targets[0][:2],
            ("trusted-vault-ui", "original-gateway-id"),
        )
        ui.verify_identity.assert_called_once_with(item)

    def test_missing_vault_ui_refuses_resume(self):
        ui = types.ModuleType("bootstrap_vault_ui")
        ui.NAME = "trusted-vault-ui"
        ui.inspect = MagicMock(return_value=None)
        ui.verify_identity = MagicMock()

        with patch.dict("sys.modules", {"bootstrap_vault_ui": ui}):
            with self.assertRaisesRegex(
                unified.UnifiedStartupError, "Missing"
            ):
                unified.guard_resume("vault-ui")

        ui.verify_identity.assert_not_called()

    def test_resume_ready_is_keep(self):
        with patch.object(unified, "checked", return_value=True), \
             patch.object(unified, "guard_resume") as guard, \
             contextlib.redirect_stdout(io.StringIO()):
            unified.resume("nginx")
        guard.assert_not_called()

    def test_resume_stopped_requires_identity(self):
        with self.assertRaisesRegex(unified.UnifiedStartupError, "Missing"):
            unified.verify_stopped_container(None, lambda _: None, "postgres")
        with self.assertRaisesRegex(unified.UnifiedStartupError, "Untrusted"):
            unified.verify_stopped_container({"State":{"Status":"exited"}},
                 lambda _: (_ for _ in ()).throw(ValueError("secret!")), "postgres")

    def test_resume_rejects_unknown_state(self):
        with self.assertRaisesRegex(unified.UnifiedStartupError, "Unexpected"):
            unified.verify_stopped_container({"State":{"Status":"restarting"}}, lambda _: None, "postgres")

    def test_resume_rejects_inconsistent_docker_state(self):
        with self.assertRaisesRegex(unified.UnifiedStartupError, "Unexpected"):
            unified.verify_stopped_container(
                {"State": {"Running": True, "Status": "exited"}}, lambda _: None, "backend"
            )

    def test_resume_accepts_known_stopped(self):
        self.assertEqual(unified.verify_stopped_container(
            {"State":{"Running":False,"Status":"exited"}}, lambda _: None, "postgres"), "stopped")

    def test_resume_refuses_running_but_unhealthy(self):
        pg = types.ModuleType("bootstrap_postgres")
        pg.NAME = "known-pg"
        pg.exact_container = lambda _: {"Id":"unchanged", "State":{"Running":True,"Status":"running"}}
        pg.verify_identity = lambda _: None
        with patch.dict("sys.modules", {"bootstrap_postgres":pg}):
            with self.assertRaisesRegex(unified.UnifiedStartupError, "already running but unhealthy"):
                unified.guard_resume("postgres")

    def test_recovery_wait_only_for_running_backend_agent(self):
        targets = (("agent", "id", lambda _: None, "running"),)
        error = unified.RunningNotReady("backend-agent", targets)
        with patch.object(unified, "checked", return_value=False), \
             patch.object(unified, "guard_resume", side_effect=error), \
             patch.object(unified, "wait_recovery_backend_agent") as wait, \
             patch.object(unified, "start_existing_only") as start:
            with self.assertRaises(unified.RunningNotReady):
                unified.resume("backend-agent")
            with self.assertRaises(unified.RunningNotReady):
                unified.resume("backend", recovery=True)
            wait.assert_not_called()
            unified.resume("backend-agent", recovery=True)
            wait.assert_called_once_with(targets)
            start.assert_not_called()

    def test_recovery_wait_pins_identity_and_checks_full_readiness(self):
        agent = types.ModuleType("bootstrap_backend_agent")
        agent.NAME = "agent"
        def item(health, identity="retained"):
            return {"Id": identity, "State": {"Status": "running", "Running": True,
                                               "Health": {"Status": health}}}
        agent.inspect_agent = MagicMock(side_effect=[item("unhealthy"), item("starting"),
                                                     item("healthy"), item("healthy")])
        verify = MagicMock()
        with patch.dict("sys.modules", {"bootstrap_backend_agent": agent}), \
             patch.object(unified.time, "sleep") as sleep, \
             patch.object(unified, "require") as require, \
             patch.object(unified.subprocess, "run") as run, \
             contextlib.redirect_stdout(io.StringIO()):
            unified.wait_recovery_backend_agent((("agent", "retained", verify, "running"),))
        self.assertEqual(verify.call_count, 4)
        self.assertEqual(sleep.call_count, 2)
        require.assert_called_once_with("backend-agent")
        run.assert_not_called()

    def test_recovery_wait_refuses_changed_identity_and_unexpected_state(self):
        agent = types.ModuleType("bootstrap_backend_agent")
        agent.NAME = "agent"
        for item in (None, {"Id": "replacement"}, {"Id": "retained", "State": {
                "Running": False, "Status": "exited"}}, {"Id": "retained", "State": {
                "Running": True, "Status": "running", "Health": {"Status": "none"}}}):
            with self.subTest(item=item):
                agent.inspect_agent = MagicMock(return_value=item)
                with patch.dict("sys.modules", {"bootstrap_backend_agent": agent}), \
                     patch.object(unified, "require") as require, \
                     patch.object(unified.time, "sleep") as sleep:
                    with self.assertRaises(unified.UnifiedStartupError):
                        unified.wait_recovery_backend_agent((("agent", "retained", lambda _: None, "running"),))
                require.assert_not_called()
                sleep.assert_not_called()

    def test_recovery_wait_expires_and_does_not_retry_security_failure(self):
        agent = types.ModuleType("bootstrap_backend_agent")
        agent.NAME = "agent"
        item = {"Id": "retained", "State": {"Running": True, "Status": "running",
                                           "Health": {"Status": "unhealthy"}}}
        agent.inspect_agent = MagicMock(return_value=item)
        targets = (("agent", "retained", lambda _: None, "running"),)
        with patch.dict("sys.modules", {"bootstrap_backend_agent": agent}), \
             patch.object(unified.time, "monotonic", side_effect=[0, 61]), \
             patch.object(unified.time, "sleep") as sleep, \
             patch.object(unified, "require") as require:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "wait expired"):
                unified.wait_recovery_backend_agent(targets)
            sleep.assert_not_called()
            require.assert_not_called()
        item["State"]["Health"]["Status"] = "healthy"
        with patch.dict("sys.modules", {"bootstrap_backend_agent": agent}), \
             patch.object(unified, "require", side_effect=unified.UnifiedStartupError("security check failed")) as require, \
             patch.object(unified.time, "sleep") as sleep:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "security check failed"):
                unified.wait_recovery_backend_agent(targets)
            require.assert_called_once_with("backend-agent")
            sleep.assert_not_called()

    def test_resume_does_not_run_helper_if_guard_fails(self):
        with patch.object(unified, "checked", return_value=False), \
             patch.object(unified, "guard_resume", side_effect=unified.UnifiedStartupError("stop")), \
             patch.object(unified, "start_existing_only") as start:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "stop"):
                unified.resume("postgres")
        start.assert_not_called()

    def test_resume_uses_only_verified_direct_start(self):
        targets = (("trusted-nginx", "id1", lambda x:x, "stopped"),)
        with patch.object(unified, "checked", side_effect=[False, True]), \
             patch.object(unified, "guard_resume", return_value=targets), \
             patch.object(unified, "start_existing_only") as start, \
             contextlib.redirect_stdout(io.StringIO()):
            unified.resume("nginx")
        start.assert_called_once_with("nginx", targets)

    def test_resume_halts_on_failed_start(self):
        with patch.object(unified, "checked", return_value=False), \
             patch.object(unified, "guard_resume", return_value=(("a", "id", lambda x:x, "stopped"),)), \
             patch.object(unified, "start_existing_only", side_effect=unified.UnifiedStartupError("stop")):
            with self.assertRaisesRegex(unified.UnifiedStartupError, "stop"):
                unified.resume("backend")

    def test_resume_rejects_missing_container_id(self):
        pg = types.ModuleType("bootstrap_postgres")
        pg.NAME = "known-pg"
        pg.exact_container = lambda _: {"State":{"Running":False,"Status":"exited"}}
        pg.verify_identity = lambda _: None
        with patch.dict("sys.modules", {"bootstrap_postgres":pg}):
            with self.assertRaisesRegex(unified.UnifiedStartupError, "Missing trusted Docker ID"):
                unified.guard_resume("postgres")

    def test_start_existing_uses_only_docker_start_and_no_compose(self):
        pg = types.ModuleType("bootstrap_postgres")
        pg.LOCK = Path(__file__)
        pg.PostgresBootstrapError = type("PostgresBootstrapError", (Exception,), {})
        before = {"Id":"id1", "State":{"Running":False, "Status":"exited"}}
        after = {"Id":"id1", "State":{"Running":True, "Status":"running"}}
        pg.exact_container = MagicMock(side_effect=[before, after])
        verify = MagicMock()
        with patch.dict("sys.modules", {"bootstrap_postgres":pg}), \
             patch.object(unified.fcntl, "flock") as lock, \
             patch.object(unified.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            unified.start_existing_only("frontend", (("existing-frontend", "id1", verify, "stopped"),))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ["docker", "start", "existing-frontend"])
        self.assertIs(run.call_args.kwargs["stdout"], unified.subprocess.DEVNULL)
        self.assertIs(run.call_args.kwargs["stderr"], unified.subprocess.DEVNULL)
        self.assertEqual(verify.call_count, 2)
        self.assertGreaterEqual(lock.call_count, 1)

    def test_guard_resume_frontend_verifies_identity_and_id(self):
        frontend = types.ModuleType("bootstrap_frontend")
        frontend.NAME = "trusted-frontend"
        frontend.container = MagicMock(return_value={
            "Id":"retained-id", "State":{"Running":False,"Status":"exited"},
        })
        frontend.verify_identity = MagicMock()
        with patch.dict("sys.modules", {"bootstrap_frontend": frontend}):
            targets = unified.guard_resume("frontend")
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0][:2], ("trusted-frontend", "retained-id"))
        self.assertEqual(targets[0][3], "stopped")
        frontend.verify_identity.assert_called_once()

    def test_start_existing_refuses_replacement_after_start(self):
        pg = types.ModuleType("bootstrap_postgres")
        pg.LOCK = Path(__file__)
        pg.PostgresBootstrapError = type("PostgresBootstrapError", (Exception,), {})
        pg.exact_container = MagicMock(side_effect=[
            {"Id":"before", "State":{"Running":False,"Status":"exited"}},
            {"Id":"replaced", "State":{"Running":True,"Status":"running"}},
        ])
        with patch.dict("sys.modules", {"bootstrap_postgres":pg}), \
             patch.object(unified.fcntl, "flock"), \
             patch.object(unified.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as run:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "identity changed"):
                unified.start_existing_only("frontend", (("frontend", "before", lambda _:None, "stopped"),))
        self.assertEqual(run.call_count, 1)

    def test_start_existing_keeps_verified_running_siblings(self):
        pg = types.ModuleType("bootstrap_postgres")
        pg.LOCK = Path(__file__)
        pg.PostgresBootstrapError = type("PostgresBootstrapError", (Exception,), {})
        pg.exact_container = MagicMock(side_effect=[
            {"Id":"run-id", "State":{"Running":True,"Status":"running"}},
            {"Id":"stop-id", "State":{"Running":False,"Status":"exited"}},
            {"Id":"stop-id", "State":{"Running":True,"Status":"running"}},
        ])
        targets = (("running", "run-id", lambda _:None, "running"),
                   ("stopped", "stop-id", lambda _:None, "stopped"))
        with patch.dict("sys.modules", {"bootstrap_postgres":pg}), \
             patch.object(unified.fcntl, "flock"), \
             patch.object(unified.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            unified.start_existing_only("support-services", targets)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ["docker", "start", "stopped"])

    def test_start_existing_verifies_all_before_start(self):
        pg = types.ModuleType("bootstrap_postgres")
        pg.LOCK = Path(__file__)
        pg.PostgresBootstrapError = type("PostgresBootstrapError", (Exception,), {})
        pg.exact_container = MagicMock(side_effect=[
            {"Id":"ok", "State":{"Running":False,"Status":"exited"}},
            {"Id":"surprise", "State":{"Running":False,"Status":"exited"}},
        ])
        targets = (("a", "ok", lambda _:None, "stopped"), ("b", "expected", lambda _:None, "stopped"))
        with patch.dict("sys.modules", {"bootstrap_postgres":pg}), \
             patch.object(unified.fcntl, "flock"), \
             patch.object(unified.subprocess, "run") as run:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "Container changed"):
                unified.start_existing_only("support-services", targets)
        run.assert_not_called()

    def test_start_existing_never_starts_unhealthy_running(self):
        pg = types.ModuleType("bootstrap_postgres")
        pg.LOCK = Path(__file__)
        pg.PostgresBootstrapError = type("PostgresBootstrapError", (Exception,), {})
        pg.exact_container = MagicMock(return_value={"Id":"id1", "State":{"Running":True,"Status":"running"}})
        with patch.dict("sys.modules", {"bootstrap_postgres":pg}), \
             patch.object(unified.fcntl, "flock"), \
             patch.object(unified.subprocess, "run") as run:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "Container state changed"):
                unified.start_existing_only("frontend", (("frontend", "id1", lambda _:None, "stopped"),))
        run.assert_not_called()

    def test_start_existing_stops_on_docker_start_failure(self):
        pg = types.ModuleType("bootstrap_postgres")
        pg.LOCK = Path(__file__)
        pg.PostgresBootstrapError = type("PostgresBootstrapError", (Exception,), {})
        pg.exact_container = MagicMock(return_value={"Id":"id1", "State":{"Running":False,"Status":"exited"}})
        with patch.dict("sys.modules", {"bootstrap_postgres":pg}), \
             patch.object(unified.fcntl, "flock"), \
             patch.object(unified.subprocess, "run", return_value=types.SimpleNamespace(returncode=1)) as run:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "Docker start failed"):
                unified.start_existing_only("frontend", (("frontend", "id1", lambda _:None, "stopped"),))
        self.assertEqual(run.call_count, 1)

    def test_start_existing_honors_migration_lock(self):
        pg = types.ModuleType("bootstrap_postgres")
        pg.LOCK = Path(__file__)
        pg.PostgresBootstrapError = type("PostgresBootstrapError", (Exception,), {})
        with patch.dict("sys.modules", {"bootstrap_postgres":pg}), \
             patch.object(unified.fcntl, "flock", side_effect=BlockingIOError), \
             patch.object(unified.subprocess, "run") as run:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "provisioning/migration"):
                unified.start_existing_only("frontend", (("frontend", "id1", lambda _:None, "stopped"),))
        run.assert_not_called()

    def test_read_only_retries_recover_without_mutation(self):
        with patch.object(unified, "command", side_effect=[False, True]) as command, \
             patch.object(unified.time, "sleep") as sleep:
            self.assertTrue(unified.checked("nginx"))
        self.assertEqual([item.args[0] for item in command.call_args_list],
                         ["--check-nginx", "--check-nginx"])
        self.assertEqual(sleep.call_count, 1)
        self.assertTrue(all(item.kwargs["quiet"] for item in command.call_args_list))

    def test_read_only_persistent_failure_no_apply(self):
        with patch.object(unified, "command", return_value=False) as command, \
             patch.object(unified.time, "sleep"):
            self.assertFalse(unified.checked("nginx"))
        self.assertEqual(command.call_count, unified.READ_ONLY_ATTEMPTS)
        self.assertTrue(all(item.args[0] == "--check-nginx" for item in command.call_args_list))

    def test_start_fast_path_without_any_apply(self):
        with patch.object(unified, "command", return_value=True) as command, \
             patch.object(unified, "vault_seal_guard") as guard, \
             patch.object(unified, "guard_resume") as resume, \
             contextlib.redirect_stdout(io.StringIO()):
            unified.start()
        self.assertEqual([item.args[0] for item in command.call_args_list],
                         ["--check-host", "--check-resources", "--check-vault-ui-optional", "--check-nginx", "--check-prometheus", "--check-grafana", "--check-grafana-https"])
        guard.assert_called_once()
        resume.assert_not_called()

    def test_vault_seal_refuses_unseal(self):
        vault = types.ModuleType("bootstrap_vault")
        vault.inspect_container = lambda: {"State":{"Running":True}}
        vault.verify_identity = lambda _: None
        vault.status = lambda tries: {"initialized":True,"sealed":True}
        vault.verify_topology = lambda *_args, **_kwargs: None
        with patch.dict("sys.modules", {"bootstrap_vault": vault}):
            with self.assertRaisesRegex(unified.UnifiedStartupError, "Unseal manually"):
                unified.vault_seal_guard()

    def test_lock_is_exclusive_and_permissions_private(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "private"
            with patch.object(unified, "STATE", state), patch.object(unified, "LOCK", state / "unified.lock"):
                with unified.exclusive_orchestrator_lock():
                    self.assertEqual((state / "unified.lock").stat().st_mode & 0o777, 0o600)
                    with self.assertRaisesRegex(unified.UnifiedStartupError, "already running"):
                        with unified.exclusive_orchestrator_lock():
                            pass

    def test_symlink_lock_denied(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            target = state / "target"
            target.write_text("do not touch")
            link = state / "unified.lock"
            link.symlink_to(target)
            with patch.object(unified, "STATE", state), patch.object(unified, "LOCK", link):
                with self.assertRaisesRegex(unified.UnifiedStartupError, "safely open"):
                    with unified.exclusive_orchestrator_lock():
                        pass
            self.assertEqual(target.read_text(), "do not touch")

    def recovery_fixture(self, *, running=True, initialized=True, sealed=True, progress=0):
        vault = types.ModuleType("bootstrap_vault")
        vault.NAME = "trusted-vault"
        item = {
            "Id": "trusted-container-id",
            "State": {"Running": running, "Status": "running" if running else "exited"},
            "HostConfig": {"Privileged": False, "PortBindings": {}},
        }
        vault.inspect_container = MagicMock(return_value=item)
        vault.verify_identity = MagicMock()
        vault.status = MagicMock(return_value={
            "initialized": initialized, "sealed": sealed, "progress": progress,
        })
        vault.verify_topology = MagicMock()
        return vault, item

    def test_recovery_requires_existing_container(self):
        vault, item = self.recovery_fixture()
        vault.inspect_container.return_value = None
        with patch.dict("sys.modules", {"bootstrap_vault": vault}), \
             patch.object(unified.subprocess, "run") as cmd:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "missing"):
                unified.recovery_vault()
        cmd.assert_not_called()

    def test_recovery_rejects_untrusted_vault(self):
        vault, item = self.recovery_fixture()
        vault.verify_identity.side_effect = ValueError("sensitive internal info")
        with patch.dict("sys.modules", {"bootstrap_vault": vault}), \
             patch.object(unified.subprocess, "run") as cmd:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "inspection failed") as caught:
                unified.recovery_vault()
        self.assertNotIn("sensitive internal info", str(caught.exception))
        cmd.assert_not_called()

    def test_recovery_accepts_vault_without_published_ports(self):
        vault, item = self.recovery_fixture(sealed=False)
        with patch.dict("sys.modules", {"bootstrap_vault": vault}), \
             patch.object(unified.subprocess, "run") as cmd, \
             contextlib.redirect_stdout(io.StringIO()):
            found, state = unified.recovery_vault()
        self.assertIs(found, vault)
        self.assertFalse(state["sealed"])
        cmd.assert_not_called()

    def test_recovery_rejects_unexpected_vault_ui_ports(self):
        unsafe = [
            {"8200/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8200"}]},
            {"8200/tcp": [{"HostIp": "::", "HostPort": "8200"}]},
            {"8200/tcp": [{"HostIp": "127.0.0.2", "HostPort": "8200"}]},
            {"8200/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8201"}]},
            {
                "8200/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8200"}],
                "8201/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8201"}],
            },
        ]
        for bindings in unsafe:
            with self.subTest(bindings=bindings):
                vault, item = self.recovery_fixture()
                item["HostConfig"]["PortBindings"] = bindings
                with patch.dict("sys.modules", {"bootstrap_vault": vault}), \
                     patch.object(unified.subprocess, "run") as cmd:
                    with self.assertRaisesRegex(
                        unified.UnifiedStartupError, "privileges/ports"
                    ):
                        unified.recovery_vault()
                cmd.assert_not_called()

    def test_recovery_rejects_public_vault_ports(self):
        vault, item = self.recovery_fixture()
        item["HostConfig"]["PortBindings"] = {"8200/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8200"}]}
        with patch.dict("sys.modules", {"bootstrap_vault": vault}), \
             patch.object(unified.subprocess, "run") as cmd:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "privileges/ports"):
                unified.recovery_vault()
        cmd.assert_not_called()

    def test_recovery_rejects_uninitialized_without_provisioning(self):
        vault, item = self.recovery_fixture(initialized=False)
        with patch.dict("sys.modules", {"bootstrap_vault": vault}), \
             patch.object(unified.subprocess, "run") as cmd:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "not initialized"):
                unified.recovery_vault()
        cmd.assert_not_called()

    def test_recovery_starts_only_known_stopped_vault(self):
        vault, item = self.recovery_fixture(running=False, sealed=False)
        running = {**item, "State": {"Running": True, "Status": "running"}}
        vault.inspect_container.side_effect = [item, running]
        with patch.dict("sys.modules", {"bootstrap_vault": vault}), \
             patch.object(unified.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as cmd, \
             contextlib.redirect_stdout(io.StringIO()):
            got, state = unified.recovery_vault()
        self.assertIs(got, vault)
        self.assertFalse(state["sealed"])
        self.assertEqual(cmd.call_args.args[0], ["docker", "start", "trusted-vault"])
        self.assertIs(cmd.call_args.kwargs["stdout"], unified.subprocess.DEVNULL)
        self.assertIs(cmd.call_args.kwargs["stderr"], unified.subprocess.DEVNULL)
        vault.verify_identity.assert_called()

    def test_recovery_refuses_changed_container_after_start(self):
        vault, item = self.recovery_fixture(running=False)
        replacement = {**item, "Id": "replaced", "State": {"Running": True, "Status": "running"}}
        vault.inspect_container.side_effect = [item, replacement]
        with patch.dict("sys.modules", {"bootstrap_vault": vault}), \
             patch.object(unified.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)):
            with self.assertRaisesRegex(unified.UnifiedStartupError, "identity changed"):
                unified.recovery_vault()

    def test_unseal_already_ready_never_prompts(self):
        vault, item = self.recovery_fixture(sealed=False)
        with patch.object(unified.subprocess, "run") as cmd:
            unified.interactive_unseal(vault, {"initialized": True, "sealed": False})
        cmd.assert_not_called()

    def test_unseal_requires_tty(self):
        vault, item = self.recovery_fixture()
        with patch.object(unified.sys.stdin, "isatty", return_value=False), \
             patch.object(unified.subprocess, "run") as cmd:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "interactive TTY"):
                unified.interactive_unseal(vault, vault.status())
        cmd.assert_not_called()

    def test_unseal_two_hidden_prompts_with_no_argv_keys(self):
        vault, item = self.recovery_fixture()
        vault.status.side_effect = [
            {"initialized": True, "sealed": True, "progress": 1},
            {"initialized": True, "sealed": False, "progress": 0},
        ]
        with patch.object(unified.sys.stdin, "isatty", return_value=True), \
             patch.object(unified.sys.stdout, "isatty", return_value=True), \
             patch.object(unified.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as cmd:
            unified.interactive_unseal(vault, {"initialized": True, "sealed": True, "progress": 0})
        self.assertEqual(cmd.call_count, 2)
        for invoked in cmd.call_args_list:
            self.assertEqual(invoked.args[0], [
                "docker", "exec", "-it", "trusted-vault", "vault", "operator", "unseal"
            ])
            self.assertNotIn("input", invoked.kwargs)
            self.assertNotIn("env", invoked.kwargs)
            self.assertNotIn("capture_output", invoked.kwargs)
            self.assertNotIn("shell", invoked.kwargs)

    def test_unseal_partial_progress_needs_one_share(self):
        vault, item = self.recovery_fixture(progress=1)
        vault.status.return_value = {"initialized": True, "sealed": False, "progress": 0}
        with patch.object(unified.sys.stdin, "isatty", return_value=True), \
             patch.object(unified.sys.stdout, "isatty", return_value=True), \
             patch.object(unified.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as cmd:
            unified.interactive_unseal(vault, {"initialized": True, "sealed": True, "progress": 1})
        self.assertEqual(cmd.call_count, 1)

    def test_unseal_refuses_invalid_progress(self):
        vault, item = self.recovery_fixture()
        with patch.object(unified.sys.stdin, "isatty", return_value=True), \
             patch.object(unified.sys.stdout, "isatty", return_value=True), \
             patch.object(unified.subprocess, "run") as cmd:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "progress"):
                unified.interactive_unseal(vault, {"initialized": True, "sealed": True, "progress": 5})
        cmd.assert_not_called()

    def test_unseal_failed_cli_does_not_retry(self):
        vault, item = self.recovery_fixture()
        with patch.object(unified.sys.stdin, "isatty", return_value=True), \
             patch.object(unified.sys.stdout, "isatty", return_value=True), \
             patch.object(unified.subprocess, "run", return_value=types.SimpleNamespace(returncode=2)) as cmd:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "rejected/interrupted"):
                unified.interactive_unseal(vault, {"initialized": True, "sealed": True, "progress": 0})
        self.assertEqual(cmd.call_count, 1)

    def test_unseal_rejects_unexpected_result(self):
        vault, item = self.recovery_fixture()
        vault.status.return_value = {"initialized": True, "sealed": True, "progress": 0}
        with patch.object(unified.sys.stdin, "isatty", return_value=True), \
             patch.object(unified.sys.stdout, "isatty", return_value=True), \
             patch.object(unified.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)):
            with self.assertRaisesRegex(unified.UnifiedStartupError, "progress unexpected"):
                unified.interactive_unseal(vault, {"initialized": True, "sealed": True, "progress": 0})

    def test_recover_only_starts_application_after_unseal(self):
        events = []
        with patch.object(unified, "require", side_effect=lambda name: events.append("require:" + name)), \
             patch.object(unified, "recovery_vault", side_effect=lambda: ("vault", {"sealed": True})), \
             patch.object(unified, "interactive_unseal", side_effect=lambda *_: events.append("unseal")), \
             patch.object(unified, "start", side_effect=lambda *, recovery: events.append("start" if recovery else "wrong mode")), \
             contextlib.redirect_stdout(io.StringIO()):
            unified.recover()
        self.assertEqual(events, ["require:host", "require:resources", "unseal", "require:vault", "start"])

    def test_recover_failed_unseal_never_starts_application(self):
        with patch.object(unified, "require") as req, \
             patch.object(unified, "recovery_vault", return_value=("vault", {"sealed": True})), \
             patch.object(unified, "interactive_unseal", side_effect=unified.UnifiedStartupError("refused")), \
             patch.object(unified, "start") as start, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(unified.UnifiedStartupError, "refused"):
                unified.recover()
        self.assertEqual(req.call_count, 2)
        start.assert_not_called()

    def test_recover_main_uses_existing_lock(self):
        with patch.object(unified.os, "geteuid", return_value=1000), \
             patch.object(unified, "exclusive_orchestrator_lock") as lock, \
             patch.object(unified, "recover") as recover, \
             patch.object(unified, "preflight_once") as preflight:
            self.assertEqual(unified.main(["recover"]), 0)
        lock.assert_called_once_with()
        recover.assert_called_once_with()
        preflight.assert_called_once_with()

    def test_main_refuses_root(self):
        with patch.object(unified.os, "geteuid", return_value=0):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(unified.main(["start"]), 1)



    def test_health_report_rejects_unavailable_prometheus(self):
        checks = lambda name: name != "prometheus"

        with patch.object(unified, "checked", side_effect=checks), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(
                unified.UnifiedStartupError, "prometheus"
            ):
                unified.health_report()

        self.assertIn("FAIL: prometheus", output.getvalue())
        self.assertNotIn("RESULT: DEVELOPMENT HEALTHY", output.getvalue())

    def test_start_never_creates_missing_prometheus(self):
        def checked(name):
            return name != "prometheus"

        def resume(name):
            if name == "prometheus":
                raise unified.UnifiedStartupError(
                    "Missing Prometheus container"
                )

        with patch.object(unified, "checked", side_effect=checked), \
             patch.object(unified, "vault_seal_guard"), \
             patch.object(unified, "resume", side_effect=resume) as mocked, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(
                unified.UnifiedStartupError, "Missing Prometheus"
            ):
                unified.start()

        self.assertEqual(mocked.call_args.args[0], "prometheus")

    def test_guard_resume_prometheus_keeps_container_identity(self):
        prometheus = types.ModuleType("bootstrap_prometheus")
        prometheus.NAME = "trusted-prometheus"
        prometheus.container = MagicMock(return_value={
            "Id": "retained-id",
            "State": {"Running": False, "Status": "exited"},
        })
        prometheus.verify_identity = MagicMock()

        with patch.dict(
            "sys.modules", {"bootstrap_prometheus": prometheus}
        ):
            targets = unified.guard_resume("prometheus")

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0][:2], (
            "trusted-prometheus", "retained-id"
        ))
        self.assertEqual(targets[0][3], "stopped")
        prometheus.verify_identity.assert_called_once()


    def test_grafana_https_has_no_prepare_operation(self):
        self.assertEqual(
            unified.STAGE_BY_NAME["grafana-https"],
            ("--check-grafana-https", None),
        )
        self.assertNotIn("grafana-https", unified.RESUMABLE)
        self.assertNotIn("grafana-secrets", unified.RESUMABLE)

        with patch.object(unified.subprocess, "run") as run:
            with self.assertRaises(unified.UnifiedStartupError):
                unified.command(None)
        run.assert_not_called()

    def test_initialize_does_not_repair_failed_grafana_https(self):
        with (
            patch.object(unified, "prepare_prerequisites", side_effect=self.fake_prerequisites),
            patch.object(
                unified, "checked",
                side_effect=lambda name, **_: name != "grafana-https",
            ),
            patch.object(unified, "command") as command,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(
                unified.UnifiedStartupError, "grafana-https"
            ):
                unified.initialize(9)

        command.assert_not_called()

    def test_unified_check_rejects_failed_grafana(self):
        with (
            patch.object(
                unified, "command",
                side_effect=lambda flag, **_: flag != "--check-grafana",
            ) as command,
            patch.object(unified.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(
                unified.UnifiedStartupError, "grafana not ready"
            ):
                unified.check()

        calls = [call.args[0] for call in command.call_args_list]
        self.assertNotIn("--check-grafana-https", calls)
        self.assertTrue(all(flag.startswith("--check-") for flag in calls))

    def test_health_report_localizes_grafana_secrets(self):
        unavailable = {"grafana", "grafana-secrets"}

        with (
            patch.object(
                unified, "checked",
                side_effect=lambda name: name not in unavailable,
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            with self.assertRaisesRegex(
                unified.UnifiedStartupError, "grafana-secrets"
            ):
                unified.health_report()

        self.assertIn("FAIL: grafana-secrets", output.getvalue())
        self.assertNotIn("RESULT: DEVELOPMENT HEALTHY", output.getvalue())

    def test_health_report_localizes_grafana_agent(self):
        unavailable = {"grafana", "grafana-agent"}

        with (
            patch.object(
                unified, "checked",
                side_effect=lambda name: name not in unavailable,
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            with self.assertRaisesRegex(
                unified.UnifiedStartupError, "grafana-agent"
            ):
                unified.health_report()

        self.assertIn("FAIL: grafana-agent", output.getvalue())
        self.assertNotIn("RESULT: DEVELOPMENT HEALTHY", output.getvalue())

    def test_health_report_rejects_failed_grafana_https(self):
        with (
            patch.object(
                unified, "checked",
                side_effect=lambda name, **_: name != "grafana-https",
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            with self.assertRaisesRegex(
                unified.UnifiedStartupError, "grafana-https"
            ):
                unified.health_report()

        self.assertIn("FAIL: grafana-https", output.getvalue())
        self.assertNotIn("RESULT: DEVELOPMENT HEALTHY", output.getvalue())

    def test_guard_resume_grafana_keeps_container_identity(self):
        grafana = types.ModuleType("bootstrap_grafana")
        grafana.NAME = "trusted-grafana"
        grafana.container = MagicMock(return_value={
            "Id": "existing-grafana-id",
            "State": {"Running": False, "Status": "exited"},
        })
        grafana.verify_identity = MagicMock()

        with patch.dict("sys.modules", {"bootstrap_grafana": grafana}):
            targets = unified.guard_resume("grafana")

        self.assertEqual(len(targets), 1)
        self.assertEqual(
            targets[0][:2],
            ("trusted-grafana", "existing-grafana-id"),
        )
        self.assertEqual(targets[0][3], "stopped")
        grafana.verify_identity.assert_called_once()

    def test_guard_resume_grafana_refuses_missing_container(self):
        grafana = types.ModuleType("bootstrap_grafana")
        grafana.NAME = "trusted-grafana"
        grafana.container = MagicMock(return_value=None)
        grafana.verify_identity = MagicMock()

        with patch.dict("sys.modules", {"bootstrap_grafana": grafana}):
            with self.assertRaisesRegex(
                unified.UnifiedStartupError, "Missing"
            ):
                unified.guard_resume("grafana")

    def test_guard_resume_grafana_agent_preserves_identity(self):
        agent = types.ModuleType("bootstrap_grafana_agent")
        agent.NAME = "trusted-grafana-agent"
        agent.credential_inventory = MagicMock(return_value="present")
        agent.container = MagicMock(return_value={
            "Id": "existing-agent-id",
            "State": {"Running": False, "Status": "exited"},
        })
        agent.verify_identity = MagicMock()

        with patch.dict(
            "sys.modules", {"bootstrap_grafana_agent": agent}
        ):
            targets = unified.guard_resume("grafana-agent")

        self.assertEqual(len(targets), 1)
        self.assertEqual(
            targets[0][:2],
            ("trusted-grafana-agent", "existing-agent-id"),
        )
        self.assertEqual(targets[0][3], "stopped")
        agent.credential_inventory.assert_called_once()
        agent.verify_identity.assert_called_once()

    def test_guard_resume_grafana_agent_refuses_missing_credentials(self):
        agent = types.ModuleType("bootstrap_grafana_agent")
        agent.credential_inventory = MagicMock(return_value="missing")
        agent.container = MagicMock()

        with patch.dict(
            "sys.modules", {"bootstrap_grafana_agent": agent}
        ):
            with self.assertRaisesRegex(
                unified.UnifiedStartupError, "AppRole missing"
            ):
                unified.guard_resume("grafana-agent")

        agent.container.assert_not_called()

    def test_start_resumes_grafana_in_dependency_order(self):
        events = []

        def checked(name):
            return name != "grafana"

        def require(name):
            events.append(("require", name))

        def resume(name):
            events.append(("resume", name))

        with (
            patch.object(unified, "checked", side_effect=checked),
            patch.object(unified, "require", side_effect=require),
            patch.object(unified, "resume", side_effect=resume),
            patch.object(unified, "vault_seal_guard"),
            patch.object(unified, "command") as command,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            unified.start()

        self.assertEqual(events[-5:], [
            ("resume", "prometheus"),
            ("require", "grafana-secrets"),
            ("resume", "grafana-agent"),
            ("resume", "grafana"),
            ("require", "grafana-https"),
        ])
        command.assert_not_called()

    def test_start_never_provisions_missing_grafana_secrets(self):
        def checked(name):
            return name not in ("grafana", "grafana-secrets")

        with (
            patch.object(unified, "checked", side_effect=checked),
            patch.object(unified, "resume") as resume,
            patch.object(unified, "vault_seal_guard"),
            patch.object(unified, "command") as command,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(
                unified.UnifiedStartupError, "grafana-secrets"
            ):
                unified.start()

        resumed = [call.args[0] for call in resume.call_args_list]
        self.assertNotIn("grafana-agent", resumed)
        self.assertNotIn("grafana", resumed)
        command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
