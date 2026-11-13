"""Phase 6A orchestration tests: offline, no Docker or host modifications."""

import contextlib
import io
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import bootstrap_unified as unified


class UnifiedTests(unittest.TestCase):
    def test_fixed_stage_order(self):
        self.assertEqual([s[0] for s in unified.STAGES], [
            "host", "resources", "vault", "vault-config", "vault-secrets",
            "postgres", "postgres-roles", "migrations", "backend-agent",
            "backend", "support-services", "frontend", "nginx",
        ])

    def test_unknown_command_denied(self):
        with self.assertRaises(unified.UnifiedStartupError):
            unified.command("--delete-volumes")

    def test_command_check_is_quiet(self):
        with patch.object(unified.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as run:
            self.assertTrue(unified.command("--check-nginx", quiet=True))
        self.assertEqual(run.call_args.args[0], [str(unified.BOOTSTRAP), "--check-nginx"])
        self.assertEqual(run.call_args.kwargs["stdout"], unified.subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["stderr"], unified.subprocess.DEVNULL)

    def test_command_failed_check(self):
        with patch.object(unified.subprocess, "run", return_value=types.SimpleNamespace(returncode=1)):
            self.assertFalse(unified.command("--check-nginx", quiet=True))

    def test_check_all_is_read_only(self):
        with patch.object(unified, "command", return_value=True) as cmd, contextlib.redirect_stdout(io.StringIO()):
            unified.check()
        self.assertEqual([call.args[0] for call in cmd.call_args_list],
                         ["--check-host", "--check-resources", "--check-nginx"])
        self.assertTrue(all(call.kwargs["quiet"] for call in cmd.call_args_list))

    def test_check_stops_on_first_failure(self):
        with patch.object(unified, "command", return_value=False) as cmd, \
             patch.object(unified.time, "sleep") as sleep:
            with self.assertRaises(unified.UnifiedStartupError):
                unified.check()
        self.assertEqual(cmd.call_count, unified.READ_ONLY_ATTEMPTS)
        self.assertEqual(sleep.call_count, unified.READ_ONLY_ATTEMPTS - 1)

    def test_initialization_requires_tty(self):
        with patch.object(unified.sys.stdin, "isatty", return_value=False):
            with self.assertRaises(unified.UnifiedStartupError):
                unified.initialize()

    def test_initialization_all_ready_has_no_apply(self):
        with patch.object(unified.sys.stdin, "isatty", return_value=True), \
             patch.object(unified, "command", return_value=True) as cmd, \
             contextlib.redirect_stdout(io.StringIO()):
            unified.initialize()
        self.assertEqual([call.args[0] for call in cmd.call_args_list],
                         [stage[1] for stage in unified.STAGES])

    def test_initialization_prepares_only_missing_stage(self):
        responses = [False] * unified.READ_ONLY_ATTEMPTS + [True, True] + [True] * (len(unified.STAGES) - 1)
        with patch.object(unified.sys.stdin, "isatty", return_value=True), \
             patch.object(unified, "command", side_effect=responses) as cmd, \
             patch.object(unified.time, "sleep"), \
             contextlib.redirect_stdout(io.StringIO()):
            unified.initialize()
        self.assertEqual([call.args[0] for call in cmd.call_args_list][:5],
                         ["--check-host"] * unified.READ_ONLY_ATTEMPTS + ["--prepare-host", "--check-host"])
        self.assertNotIn("--prepare-vault", [call.args[0] for call in cmd.call_args_list])

    def test_initialization_halts_on_denied_prepare(self):
        with patch.object(unified.sys.stdin, "isatty", return_value=True), \
             patch.object(unified, "command", side_effect=[False] * (unified.READ_ONLY_ATTEMPTS + 1)) as cmd, \
             patch.object(unified.time, "sleep"):
            with self.assertRaises(unified.UnifiedStartupError):
                unified.initialize()
        self.assertEqual(cmd.call_count, unified.READ_ONLY_ATTEMPTS + 1)

    def test_routine_does_not_run_any_prepare_flag(self):
        with patch.object(unified, "checked", return_value=True) as ck, \
             patch.object(unified, "vault_seal_guard") as vault, \
             contextlib.redirect_stdout(io.StringIO()):
            unified.start()
        self.assertEqual([call.args[0] for call in ck.call_args_list],
                         ["host", "resources", "nginx"])
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

    def test_resume_accepts_known_stopped(self):
        self.assertEqual(unified.verify_stopped_container(
            {"State":{"Running":False,"Status":"exited"}}, lambda _: None, "postgres"), "stopped")

    def test_resume_refuses_running_but_unhealthy(self):
        pg = types.ModuleType("bootstrap_postgres")
        pg.NAME = "known-pg"
        pg.exact_container = lambda _: {"State":{"Running":True,"Status":"running"}}
        pg.verify_identity = lambda _: None
        with patch.dict("sys.modules", {"bootstrap_postgres":pg}):
            with self.assertRaisesRegex(unified.UnifiedStartupError, "already running but unhealthy"):
                unified.guard_resume("postgres")

    def test_resume_does_not_run_helper_if_guard_fails(self):
        with patch.object(unified, "checked", return_value=False), \
             patch.object(unified, "guard_resume", side_effect=unified.UnifiedStartupError("stop")), \
             patch.object(unified.subprocess, "run") as run:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "stop"):
                unified.resume("postgres")
        run.assert_not_called()

    def test_resume_uses_apply_helper_not_prepare(self):
        with patch.object(unified, "checked", side_effect=[False, True]), \
             patch.object(unified, "guard_resume", return_value=("stopped",)), \
             patch.object(unified.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            unified.resume("nginx")
        self.assertEqual(run.call_args.args[0], [unified.sys.executable,
           str(unified.ROOT / "scripts/dev/bootstrap_nginx.py"), "apply"])

    def test_resume_halts_on_failed_apply(self):
        with patch.object(unified, "checked", return_value=False), \
             patch.object(unified, "guard_resume", return_value=("stopped",)), \
             patch.object(unified.subprocess, "run", return_value=types.SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(unified.UnifiedStartupError, "resume failed"):
                unified.resume("backend")

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
                         ["--check-host", "--check-resources", "--check-nginx"])
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
             patch.object(unified, "start", side_effect=lambda: events.append("start")), \
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
             patch.object(unified, "recover") as recover:
            self.assertEqual(unified.main(["recover"]), 0)
        lock.assert_called_once_with()
        recover.assert_called_once_with()

    def test_main_refuses_root(self):
        with patch.object(unified.os, "geteuid", return_value=0):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(unified.main(["start"]), 1)


if __name__ == "__main__":
    unittest.main()
