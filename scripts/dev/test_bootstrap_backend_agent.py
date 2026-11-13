"""Offline Phase 5A security, idempotency and topology tests (no Docker)."""

import contextlib
import copy
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bootstrap_backend_agent as agent


def item(status="running", health="healthy"):
    return {
        "Name": "/" + agent.NAME,
        "Config": {
            "Image": agent.VAULT_IMAGE,
            "User": "100:20000",
            "Env": ["VAULT_ADDR=https://vault:8200"],
            "Labels": {
                "com.docker.compose.project": "transcendence-app-dev",
                "com.docker.compose.service": "vault-backend-agent",
                "com.docker.compose.project.config_files": str(agent.pg.COMPOSE_FILE),
                "com.docker.compose.project.working_dir": str(agent.pg.ROOT),
            },
        },
        "State": {"Status": status, "Running": status == "running", "Health": {"Status": health}},
        "NetworkSettings": {"Networks": {agent.PRIVATE_NETWORK: {}}},
        "HostConfig": {"Privileged": False, "PortBindings": {}},
        "Mounts": [
            {"Type": "volume", "Destination": "/vault/auth", "Name": agent.AUTH_VOLUME, "RW": False},
            {"Type": "volume", "Destination": "/vault/rendered", "Name": agent.RENDERED_VOLUME, "RW": True},
            {"Type": "bind", "Destination": "/vault/tls/ca.crt",
             "Source": str(agent.config.vault.STATE / "tls/ca.crt"), "RW": False},
        ],
    }


class BackendAgentTests(unittest.TestCase):
    def test_safe_env_strips_credentials(self):
        with patch.dict(os.environ, {
            "VAULT_TOKEN": "sensitive", "DATABASE_URL": "sensitive",
            "POSTGRES_PASSWORD": "sensitive", "FT_APP_COMPOSE_OVERRIDE": "bad",
        }):
            env = agent.safe_env()
        self.assertNotIn("VAULT_TOKEN", env)
        self.assertNotIn("DATABASE_URL", env)
        self.assertNotIn("POSTGRES_PASSWORD", env)
        self.assertNotIn("FT_APP_COMPOSE_OVERRIDE", env)
        self.assertEqual(env["FT_APP_COMPOSE_PROJECT"], "transcendence-app-dev")

    def test_backend_setup_uses_only_fixed_vault_compose(self):
        cmd = agent.vault_compose("--profile", "setup", "run", "--rm", "--no-deps", "-T", "vault-backend-provision")
        self.assertIn("/dev/null", cmd)
        self.assertIn("vault-backend-provision", cmd)
        self.assertIn("--no-deps", cmd)
        self.assertNotIn("VAULT_TOKEN", str(cmd))

    def test_credential_probe_missing(self):
        with patch.object(agent, "run", return_value=SimpleNamespace(returncode=2)) as mock:
            self.assertEqual(agent.credential_inventory(), "missing")
        self.assertEqual(mock.call_args_list[0].args[0], ["docker", "volume", "inspect", agent.AUTH_VOLUME])
        cmd = mock.call_args.args[0]
        self.assertIn("--network", cmd)
        self.assertIn("none", cmd)
        self.assertIn("readonly", str(cmd))
        self.assertIn("--read-only", cmd)
        self.assertIn("stat -c %a", str(cmd))

    def test_missing_auth_volume_refuses_without_creating_container(self):
        def fake(args, **kwargs):
            raise agent.BackendAgentError("Volume missing")
        with patch.object(agent, "run", side_effect=fake) as run:
            with self.assertRaises(agent.BackendAgentError):
                agent.credential_inventory()
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0][:3], ["docker", "volume", "inspect"])

    def test_credential_probe_present(self):
        with patch.object(agent, "run", return_value=SimpleNamespace(returncode=0)):
            self.assertEqual(agent.credential_inventory(), "present")

    def test_credential_probe_partial_refused(self):
        with patch.object(agent, "run", return_value=SimpleNamespace(returncode=3)):
            with self.assertRaises(agent.BackendAgentError):
                agent.credential_inventory()

    def test_existing_approle_never_reissues(self):
        with patch.object(agent, "credential_inventory", return_value="present"), \
             patch.object(agent.config, "recovery_token") as token, \
             patch.object(agent, "run") as run:
            agent.ensure_credentials({})
        token.assert_not_called()
        run.assert_not_called()

    def test_initial_approle_token_only_stdin(self):
        with patch.object(agent, "credential_inventory", side_effect=["missing", "present"]), \
             patch.object(agent.config, "recovery_token", return_value="very-secret-token"), \
             patch.object(agent, "run") as run:
            agent.ensure_credentials({"FT_DEV_TLS_DIR": "/tmp/tls"})
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs["stdin"], b"very-secret-token\n")
        self.assertNotIn("very-secret-token", str(run.call_args.args[0]))
        self.assertNotIn("very-secret-token", str(run.call_args.kwargs["env"]))

    def test_incomplete_initial_approle_fails(self):
        with patch.object(agent, "credential_inventory", side_effect=["missing", "missing"]), \
             patch.object(agent.config, "recovery_token", return_value="secret"), \
             patch.object(agent, "run"):
            with self.assertRaises(agent.BackendAgentError):
                agent.ensure_credentials({})

    def test_identity_accepts_expected_topology(self):
        self.assertEqual(agent.verify_identity(item()), item())

    def test_identity_rejects_wrong_project(self):
        i = item()
        i["Config"]["Labels"]["com.docker.compose.project"] = "other"
        with self.assertRaises(agent.BackendAgentError): agent.verify_identity(i)

    def test_identity_rejects_auth_mount_rw(self):
        i = item()
        i["Mounts"][0]["RW"] = True
        with self.assertRaises(agent.BackendAgentError): agent.verify_identity(i)

    def test_identity_rejects_wrong_network(self):
        i = item()
        i["NetworkSettings"]["Networks"]["other"] = {}
        with self.assertRaises(agent.BackendAgentError): agent.verify_identity(i)

    def test_identity_rejects_wrong_ca(self):
        i = item()
        i["Mounts"][2]["Source"] = "/some/other/ca.crt"
        with self.assertRaises(agent.BackendAgentError): agent.verify_identity(i)

    def test_identity_rejects_operator_token_env(self):
        i = item()
        i["Config"]["Env"].append("VAULT_TOKEN=secret")
        with self.assertRaises(agent.BackendAgentError): agent.verify_identity(i)

    def test_agent_readiness_requires_healthy(self):
        self.assertTrue(agent.ready(item()))
        self.assertFalse(agent.ready(item(health="starting")))
        self.assertFalse(agent.ready(item(status="exited")))

    def test_rendered_secret_probe_uses_backend_uid_and_no_network(self):
        with patch.object(agent, "run") as run:
            agent.verify_rendered_access()
        self.assertEqual(run.call_args_list[0].args[0], ["docker", "volume", "inspect", agent.RENDERED_VOLUME])
        cmd = run.call_args.args[0]
        self.assertIn("10001:20000", cmd)
        self.assertIn("readonly", str(cmd))
        self.assertIn("none", cmd)
        self.assertIn("stat -c %g", str(cmd))
        self.assertNotIn("cat /vault/rendered/database_url", str(cmd))

    def test_existing_running_container_not_recreated(self):
        with patch.object(agent, "inspect_agent", return_value=item()), \
             patch.object(agent, "run") as run:
            agent.start_or_keep({})
        run.assert_not_called()

    def test_existing_exited_container_only_started(self):
        with patch.object(agent, "inspect_agent", return_value=item(status="exited")), \
             patch.object(agent, "run") as run:
            agent.start_or_keep({})
        self.assertEqual(run.call_args.args[0], ["docker", "start", agent.NAME])

    def test_new_agent_compose_creates_only_agent(self):
        with patch.object(agent, "inspect_agent", side_effect=[None, item()]), \
             patch.object(agent, "run") as run:
            agent.start_or_keep({"FT_DEV_TLS_DIR": "/state/tls"})
        cmd = run.call_args.args[0]
        self.assertIn("--no-deps", cmd)
        self.assertIn("--no-recreate", cmd)
        self.assertEqual(cmd[-1], "vault-backend-agent")
        self.assertNotIn("backend", cmd[:-1])

    def test_wait_ready_checks_render_access(self):
        with patch.object(agent, "inspect_agent", return_value=item()), \
             patch.object(agent, "verify_rendered_access") as access:
            agent.wait_agent(attempts=1)
        access.assert_called_once()

    def test_unhealthy_agent_not_silently_restarted(self):
        with patch.object(agent, "inspect_agent", return_value=item(health="unhealthy")), \
             patch.object(agent, "verify_rendered_access"):
            with self.assertRaises(agent.BackendAgentError): agent.wait_agent(attempts=1)

    def test_plan_no_secret_reads_no_mutation(self):
        with patch.object(agent, "inspect_agent", return_value=None), \
             patch.object(agent, "ensure_credentials") as credentials, \
             patch.object(agent, "start_or_keep") as start, \
             patch.object(agent.config, "recovery_token") as token:
            self.assertEqual(agent.main(["plan"]), 0)
        credentials.assert_not_called()
        start.assert_not_called()
        token.assert_not_called()

    def test_check_never_mutates(self):
        with patch.object(agent, "require_prerequisites"), \
             patch.object(agent, "credential_inventory", return_value="present"), \
             patch.object(agent, "inspect_agent", return_value=item()), \
             patch.object(agent, "verify_rendered_access"), \
             patch.object(agent, "apply") as apply:
            self.assertEqual(agent.main(["check"]), 0)
        apply.assert_not_called()

    def test_apply_fails_before_mutation_if_migrations_missing(self):
        with patch.object(agent.migrations, "main", return_value=1), \
             patch.object(agent, "apply") as apply, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(agent.main(["apply"]), 1)
        apply.assert_not_called()

    def test_sanitized_failure_does_not_print_secret(self):
        raw = SimpleNamespace(returncode=45, stdout=b"very-secret", stderr=b"very-secret")
        with patch.object(agent.subprocess, "run", return_value=raw):
            with self.assertRaises(agent.BackendAgentError) as err:
                agent.run(["docker", "ps"])
        self.assertNotIn("very-secret", str(err.exception))

    def test_sanitized_timeout_does_not_print_secret(self):
        with patch.object(agent.subprocess, "run", side_effect=subprocess.TimeoutExpired(
                cmd=["docker", "ps"], timeout=10, output=b"secret")):
            with self.assertRaises(agent.BackendAgentError) as err:
                agent.run(["docker", "ps"])
        self.assertNotIn("secret", str(err.exception))

    def test_apply_holds_lock_and_calls_exact_sequence(self):
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / "lock"
            lock.write_text("lock")
            with patch.object(agent.pg, "LOCK", lock), \
                 patch.object(agent, "inspect_agent", return_value=None), \
                 patch.object(agent, "ensure_credentials") as creds, \
                 patch.object(agent, "start_or_keep") as start, \
                 patch.object(agent, "wait_agent") as wait:
                agent.apply()
        creds.assert_called_once()
        start.assert_called_once()
        wait.assert_called_once()


if __name__ == "__main__":
    unittest.main()
