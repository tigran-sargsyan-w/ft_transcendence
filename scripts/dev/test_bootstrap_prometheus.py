"""Offline Prometheus bootstrap security tests."""

import unittest
from unittest.mock import patch

import bootstrap_prometheus as prometheus


def fixture():
    return {
        "Id": "trusted-prometheus-id",
        "Name": "/" + prometheus.NAME,
        "Config": {
            "Image": prometheus.IMAGE,
            "User": "65534:65534",
            "Cmd": prometheus.COMMAND.copy(),
            "Env": ["PATH=/usr/bin"],
            "Labels": {
                "com.docker.compose.project": prometheus.pg.PROJECT,
                "com.docker.compose.service": prometheus.SERVICE,
                "com.docker.compose.project.config_files":
                    str(prometheus.pg.COMPOSE_FILE),
                "com.docker.compose.project.working_dir":
                    str(prometheus.pg.ROOT),
            },
        },
        "HostConfig": {
            "Privileged": False,
            "PortBindings": {},
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Tmpfs": {"/tmp": "rw,nosuid,nodev,noexec,size=32m"},
        },
        "NetworkSettings": {
            "Networks": {prometheus.NETWORK: {}},
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": str(prometheus.CONFIG_DIR),
                "Destination": "/etc/prometheus",
                "RW": False,
            },
            {
                "Type": "volume",
                "Name": prometheus.VOLUME,
                "Destination": "/prometheus",
                "RW": True,
            },
        ],
        "State": {"Running": True, "Status": "running"},
    }


class PrometheusBootstrapTests(unittest.TestCase):
    def test_expected_identity(self):
        self.assertIsNotNone(prometheus.verify_identity(fixture()))

    def test_other_network_rejected(self):
        item = fixture()
        item["NetworkSettings"]["Networks"]["untrusted"] = {}
        with self.assertRaises(prometheus.PrometheusBootstrapError):
            prometheus.verify_identity(item)

    def test_published_port_rejected(self):
        item = fixture()
        item["HostConfig"]["PortBindings"] = {"9090/tcp": [{"HostPort": "9090"}]}
        with self.assertRaises(prometheus.PrometheusBootstrapError):
            prometheus.verify_identity(item)

    def test_privileged_rejected(self):
        item = fixture()
        item["HostConfig"]["Privileged"] = True
        with self.assertRaises(prometheus.PrometheusBootstrapError):
            prometheus.verify_identity(item)

    def test_writable_config_rejected(self):
        item = fixture()
        item["Mounts"][0]["RW"] = True
        with self.assertRaises(prometheus.PrometheusBootstrapError):
            prometheus.verify_identity(item)

    def test_docker_socket_rejected(self):
        item = fixture()
        item["Mounts"].append({
            "Type": "bind",
            "Source": "/var/run/docker.sock",
            "Destination": "/var/run/docker.sock",
            "RW": False,
        })
        with self.assertRaises(prometheus.PrometheusBootstrapError):
            prometheus.verify_identity(item)

    def test_unsafe_command_rejected(self):
        item = fixture()
        item["Config"]["Cmd"].append("--web.enable-admin-api")
        with self.assertRaises(prometheus.PrometheusBootstrapError):
            prometheus.verify_identity(item)

    def test_read_write_rootfs_rejected(self):
        item = fixture()
        item["HostConfig"]["ReadonlyRootfs"] = False
        with self.assertRaises(prometheus.PrometheusBootstrapError):
            prometheus.verify_identity(item)

    def test_credentials_rejected(self):
        item = fixture()
        item["Config"]["Env"].append("VAULT_TOKEN=secret")
        with self.assertRaises(prometheus.PrometheusBootstrapError):
            prometheus.verify_identity(item)

    def test_existing_running_container_is_not_recreated(self):
        with patch.object(prometheus, "container", return_value=fixture()), \
             patch.object(prometheus.support, "run") as run:
            prometheus.start_or_keep({})
        run.assert_not_called()

    def test_untrusted_container_is_not_started(self):
        item = fixture()
        item["Config"]["Image"] = "untrusted:latest"

        with patch.object(prometheus, "container", return_value=item), \
             patch.object(prometheus.support, "run") as run:
            with self.assertRaises(prometheus.PrometheusBootstrapError):
                prometheus.start_or_keep({})
        run.assert_not_called()

    def test_missing_resources_refuse_startup(self):
        with patch.object(prometheus.resources, "validate_compose_contract"), \
             patch.object(
                 prometheus.resources, "scan", return_value={"volume": False}
             ), \
             patch.object(prometheus.support, "main") as support:
            with self.assertRaises(prometheus.PrometheusBootstrapError):
                prometheus.require_prerequisites()
        support.assert_not_called()



    def test_http_probe_requires_healthy_scrape_and_real_metrics(self):
        with patch.object(prometheus.support, "run") as run:
            prometheus.probe_http()

        args = run.call_args.args[0]

        self.assertEqual(
            args[:5],
            [
                "docker",
                "exec",
                f"{prometheus.pg.PROJECT}-backend-1",
                "node",
                "-e",
            ],
        )

        script = args[5]

        self.assertIn(
            "http://collector:3001/metrics",
            script,
        )
        self.assertIn(
            "matching[0].health !== 'up'",
            script,
        )
        self.assertIn(
            "ft_demo_lab_present",
            script,
        )
        self.assertIn(
            "samples.length !== 1",
            script,
        )


if __name__ == "__main__":
    unittest.main()
