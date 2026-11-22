"""Offline security regression tests for the Vault UI gateway."""

import copy
import unittest
from unittest.mock import patch

import bootstrap_vault_ui as gateway


PORTS = {
    "8200/tcp": [
        {"HostIp": "127.0.0.1", "HostPort": "8200"}
    ]
}


def fixture():
    return {
        "Id": "trusted-gateway-id",
        "Name": "/" + gateway.NAME,
        "Config": {
            "Image": gateway.IMAGE,
            "User": "99:99",
            "Env": ["PATH=/usr/bin"],
            "Labels": {
                "com.docker.compose.project": gateway.PROJECT,
                "com.docker.compose.service": gateway.SERVICE,
                "com.docker.compose.project.config_files":
                    str(gateway.COMPOSE),
                "com.docker.compose.project.working_dir":
                    str(gateway.COMPOSE.parent),
            },
        },
        "HostConfig": {
            "Privileged": False,
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "SecurityOpt": ["no-new-privileges:true"],
            "PortBindings": copy.deepcopy(PORTS),
            "Tmpfs": {
                "/tmp": "rw,noexec,nosuid,size=16m"
            },
            "RestartPolicy": {"Name": "no"},
        },
        "NetworkSettings": {
            "Networks": {
                gateway.PRIVATE: {},
                gateway.EDGE: {},
            },
            "Ports": copy.deepcopy(PORTS),
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": str(gateway.CONFIG),
                "Destination": gateway.TARGET,
                "RW": False,
            }
        ],
        "State": {"Running": True, "Status": "running"},
    }


class GatewaySecurityTests(unittest.TestCase):

    def test_trusted_container_accepted(self):
        gateway.verify_identity(fixture())

    def test_public_port_rejected(self):
        item = fixture()
        item["HostConfig"]["PortBindings"]["8200/tcp"][0][
            "HostIp"
        ] = "0.0.0.0"

        with self.assertRaises(gateway.GatewayError):
            gateway.verify_identity(item)

    def test_actual_published_port_mismatch_rejected(self):
        item = fixture()
        item["NetworkSettings"]["Ports"]["8200/tcp"][0][
            "HostIp"
        ] = "0.0.0.0"

        with self.assertRaises(gateway.GatewayError):
            gateway.verify_identity(item)

    def test_extra_network_rejected(self):
        item = fixture()
        item["NetworkSettings"]["Networks"]["untrusted"] = {}

        with self.assertRaises(gateway.GatewayError):
            gateway.verify_identity(item)

    def test_wrong_compose_owner_rejected(self):
        item = fixture()
        item["Config"]["Labels"][
            "com.docker.compose.project"
        ] = "untrusted"

        with self.assertRaises(gateway.GatewayError):
            gateway.verify_identity(item)

    def test_unsafe_privileges_rejected(self):
        modifications = {
            "Privileged": True,
            "ReadonlyRootfs": False,
            "CapDrop": [],
            "CapAdd": ["NET_ADMIN"],
            "SecurityOpt": [],
            "RestartPolicy": {"Name": "always"},
            "Tmpfs": {},
        }

        for key, value in modifications.items():
            with self.subTest(setting=key):
                item = fixture()
                item["HostConfig"][key] = value

                with self.assertRaises(gateway.GatewayError):
                    gateway.verify_identity(item)

    def test_writable_config_rejected(self):
        item = fixture()
        item["Mounts"][0]["RW"] = True

        with self.assertRaises(gateway.GatewayError):
            gateway.verify_identity(item)

    def test_docker_socket_mount_rejected(self):
        item = fixture()
        item["Mounts"].append({
            "Type": "bind",
            "Source": "/var/run/docker.sock",
            "Destination": "/var/run/docker.sock",
            "RW": False,
        })

        with self.assertRaises(gateway.GatewayError):
            gateway.verify_identity(item)

    def test_credentials_in_environment_rejected(self):
        for secret in (
            "VAULT_TOKEN=secret",
            "DATABASE_URL=secret",
            "POSTGRES_PASSWORD=secret",
            "GF_SECURITY_ADMIN_PASSWORD=secret",
        ):
            with self.subTest(variable=secret.split("=")[0]):
                item = fixture()
                item["Config"]["Env"].append(secret)

                with self.assertRaises(gateway.GatewayError):
                    gateway.verify_identity(item)

    def test_untrusted_container_not_started(self):
        item = fixture()
        item["HostConfig"]["Privileged"] = True

        with (
            patch.object(
                gateway, "compose",
                return_value=(["docker", "compose"], {}),
            ),
            patch.object(gateway, "verify_network"),
            patch.object(gateway, "inspect", return_value=item),
            patch.object(gateway, "command") as command,
            patch.object(gateway, "check") as check,
        ):
            with self.assertRaises(gateway.GatewayError):
                gateway.apply()

            self.assertEqual(command.call_count, 1)
            check.assert_not_called()

            for call in command.call_args_list:
                args = call.args[0]
                self.assertNotIn("start", args)
                self.assertNotIn("up", args)

    def test_running_gateway_not_recreated(self):
        item = fixture()

        with (
            patch.object(
                gateway, "compose",
                return_value=(["docker", "compose"], {}),
            ),
            patch.object(gateway, "verify_network"),
            patch.object(gateway, "inspect", return_value=item),
            patch.object(gateway, "command") as command,
            patch.object(gateway, "check") as check,
        ):
            gateway.apply()

            self.assertEqual(command.call_count, 1)
            check.assert_called_once_with()

            for call in command.call_args_list:
                args = call.args[0]
                self.assertNotIn("start", args)
                self.assertNotIn("up", args)

    def test_strict_cli_check_refuses_disabled_intent_without_docker_changes(self):
        with (
            patch.object(gateway.os, "geteuid", return_value=1000),
            patch.object(gateway, "desired_enabled", return_value=False),
            patch.object(gateway, "check") as check,
            patch.object(gateway, "command") as command,
        ):
            with patch("sys.argv", ["bootstrap_vault_ui.py", "check"]):
                self.assertEqual(gateway.main(), 1)
        check.assert_not_called()
        command.assert_not_called()

    def test_check_refuses_stopped_gateway(self):
        item = fixture()
        item["State"] = {
            "Running": False,
            "Status": "exited",
        }

        with (
            patch.object(gateway, "compose"),
            patch.object(gateway, "inspect", return_value=item),
            patch.object(gateway, "verify_network"),
            patch.object(gateway, "probe_ui") as probe,
        ):
            with self.assertRaises(gateway.GatewayError):
                gateway.check()

            probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
