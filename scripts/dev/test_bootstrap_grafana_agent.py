"""Offline Grafana Vault Agent security and lifecycle tests."""

import copy
import unittest

from types import SimpleNamespace
from unittest.mock import patch

import bootstrap_grafana_agent as mod


def trusted_agent():
    mounts = [
        {
            "Type": "bind",
            "Source": str(source),
            "Destination": destination,
            "RW": False,
        }
        for destination, source in (
            ("/vault/config/grafana.hcl", mod.AGENT_CONFIG),
            ("/vault/scripts/grafana-agent.sh", mod.SCRIPT),
            ("/vault/tls/ca.crt", mod.CA_FILE),
        )
    ]

    mounts.extend([
        {
            "Type": "volume",
            "Name": mod.AUTH_VOLUME,
            "Destination": "/vault/auth",
            "RW": False,
        },
        {
            "Type": "volume",
            "Name": mod.RENDERED_VOLUME,
            "Destination": "/vault/rendered",
            "RW": True,
        },
    ])

    return {
        "Name": "/" + mod.NAME,
        "Id": "test-container-id",
        "Config": {
            "Image": mod.IMAGE,
            "User": "100:20030",
            "Entrypoint": mod.ENTRYPOINT,
            "Cmd": ["start"],
            "Healthcheck": {"Test": mod.HEALTHCHECK},
            "Labels": {
                "com.docker.compose.project": mod.pg.PROJECT,
                "com.docker.compose.service": mod.SERVICE,
                "com.docker.compose.project.config_files":
                    str(mod.pg.COMPOSE_FILE),
                "com.docker.compose.project.working_dir":
                    str(mod.pg.ROOT),
            },
            "Env": [
                "VAULT_ADDR=https://vault:8200",
                "VAULT_CACERT=/vault/tls/ca.crt",
            ],
        },
        "HostConfig": {
            "Privileged": False,
            "PortBindings": {},
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "CapAdd": [],
            "SecurityOpt": ["no-new-privileges:true"],
            "Tmpfs": {path: "" for path in mod.TMPFS_PATHS},
        },
        "NetworkSettings": {
            "Networks": {mod.NETWORK: {}},
        },
        "Mounts": mounts,
        "State": {
            "Running": True,
            "Status": "running",
            "Health": {"Status": "healthy"},
        },
    }


class AgentTests(unittest.TestCase):
    def test_trusted_identity(self):
        item = trusted_agent()
        self.assertIs(mod.verify_identity(item), item)
        self.assertTrue(mod.ready(item))

    def test_unsafe_runtime_rejected(self):
        for field, value in (
            ("Privileged", True),
            ("ReadonlyRootfs", False),
            ("CapDrop", []),
            ("CapAdd", ["SYS_ADMIN"]),
            ("PortBindings", {"8200/tcp": [{}]}),
            ("Tmpfs", {}),
        ):
            with self.subTest(field=field):
                item = trusted_agent()
                item["HostConfig"][field] = value

                with self.assertRaises(mod.GrafanaAgentError):
                    mod.verify_identity(item)

    def test_additional_network_rejected(self):
        item = trusted_agent()
        item["NetworkSettings"]["Networks"]["default"] = {}

        with self.assertRaises(mod.GrafanaAgentError):
            mod.verify_identity(item)

    def test_unexpected_mount_rejected(self):
        item = trusted_agent()
        item["Mounts"].append({
            "Type": "bind",
            "Source": "/var/run/docker.sock",
            "Destination": "/var/run/docker.sock",
            "RW": True,
        })

        with self.assertRaises(mod.GrafanaAgentError):
            mod.verify_identity(item)

    def test_writable_auth_volume_rejected(self):
        item = trusted_agent()

        for mount in item["Mounts"]:
            if mount["Destination"] == "/vault/auth":
                mount["RW"] = True

        with self.assertRaises(mod.GrafanaAgentError):
            mod.verify_identity(item)

    def test_token_in_environment_rejected(self):
        item = trusted_agent()
        item["Config"]["Env"].append("VAULT_TOKEN=fake")

        with self.assertRaises(mod.GrafanaAgentError):
            mod.verify_identity(item)

    def test_existing_credentials_are_preserved(self):
        with patch.object(mod, "credential_inventory",
                          return_value="present"), \
             patch.object(mod.config, "recovery_token") as token, \
             patch.object(mod.agent, "run") as run:

            mod.ensure_credentials({})

        token.assert_not_called()
        run.assert_not_called()

    def test_existing_agent_blocks_new_secret_id(self):
        with patch.object(mod, "credential_inventory",
                          return_value="missing"), \
             patch.object(mod, "container",
                          return_value=trusted_agent()), \
             patch.object(mod.config, "recovery_token") as token:

            with self.assertRaises(mod.GrafanaAgentError):
                mod.ensure_credentials({})

        token.assert_not_called()

    def test_new_credentials_receive_token_only_on_stdin(self):
        with patch.object(mod, "credential_inventory",
                          side_effect=["missing", "present"]), \
             patch.object(mod, "container", return_value=None), \
             patch.object(mod.config, "recovery_token",
                          return_value="private-test-token"), \
             patch.object(mod.agent, "app_compose",
                          return_value=["docker", "compose"]), \
             patch.object(mod.agent, "run") as run:

            mod.ensure_credentials({})

        self.assertEqual(
            run.call_args.kwargs["stdin"],
            b"private-test-token\n",
        )

        self.assertNotIn(
            "private-test-token",
            str(run.call_args.args),
        )

    def test_running_agent_is_never_recreated(self):
        with patch.object(mod, "container",
                          return_value=trusted_agent()), \
             patch.object(mod.agent, "run") as run:

            mod.start_or_keep({})

        run.assert_not_called()

    def test_stopped_agent_is_started_by_name(self):
        item = trusted_agent()
        item["State"]["Status"] = "exited"
        item["State"]["Running"] = False

        with patch.object(mod, "container", return_value=item), \
             patch.object(mod.agent, "run") as run:

            mod.start_or_keep({})

        self.assertEqual(
            run.call_args.args[0],
            ["docker", "start", mod.NAME],
        )

    def test_unhealthy_agent_fails_read_only_check(self):
        item = trusted_agent()
        item["State"]["Health"]["Status"] = "unhealthy"

        with patch.object(mod, "require_prerequisites"), \
             patch.object(mod, "credential_inventory",
                          return_value="present"), \
             patch.object(mod, "container", return_value=item), \
             patch.object(mod, "verify_delivery") as delivery:

            with self.assertRaises(mod.GrafanaAgentError):
                mod.check()

        delivery.assert_not_called()

    def test_rendered_probe_has_no_network_or_auth_volume(self):
        with patch.object(mod.agent, "run") as run:
            mod.verify_rendered_access()

        command = run.call_args.args[0]

        self.assertIn("none", command)
        self.assertIn("472:20030", command)
        self.assertIn("--read-only", command)
        self.assertNotIn(mod.AUTH_VOLUME, str(command))
        self.assertIn(mod.RENDERED_VOLUME, str(command))

    def test_credential_inventory_probes_read_only(self):
        with patch.object(
            mod.agent,
            "run",
            side_effect=[
                SimpleNamespace(returncode=0),
                SimpleNamespace(returncode=2),
            ],
        ) as run:
            self.assertEqual(mod.credential_inventory(), "missing")

        command = run.call_args.args[0]

        self.assertIn("none", command)
        self.assertIn(
            f"type=volume,source={mod.AUTH_VOLUME},target=/vault/auth,readonly",
            command,
        )



    def test_inherited_vault_volumes_must_be_overridden(self):
        for path in ("/vault/file", "/vault/logs"):
            with self.subTest(path=path):
                item = trusted_agent()
                del item["HostConfig"]["Tmpfs"][path]

                with self.assertRaises(mod.GrafanaAgentError):
                    mod.verify_identity(item)

    def test_unexpected_tmpfs_rejected(self):
        item = trusted_agent()
        item["HostConfig"]["Tmpfs"]["/unexpected"] = ""

        with self.assertRaises(mod.GrafanaAgentError):
            mod.verify_identity(item)

    def test_inherited_anonymous_volume_rejected(self):
        item = trusted_agent()

        item["Mounts"].append({
            "Type": "volume",
            "Name": "unexpected-anonymous-volume",
            "Destination": "/vault/file",
            "RW": True,
        })

        with self.assertRaises(mod.GrafanaAgentError):
            mod.verify_identity(item)


if __name__ == "__main__":
    unittest.main()
