"""Offline tests for bounded Grafana prerequisite retries."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path("scripts/dev").resolve()))

import bootstrap_grafana as grafana


class GrafanaPrerequisiteTests(unittest.TestCase):

    def setUp(self):
        patches = [
            patch.object(
                grafana.resources,
                "validate_compose_contract",
            ),
            patch.object(
                grafana.resources,
                "scan",
                return_value={"resources": True},
            ),
            patch.object(
                grafana.grafana_agent,
                "main",
                return_value=0,
            ),
            patch.object(grafana.time, "sleep"),
        ]

        mocks = [item.start() for item in patches]

        for item in patches:
            self.addCleanup(item.stop)

        self.contract, self.scan, self.agent, self.sleep = mocks

    def test_healthy_prometheus_no_retry(self):
        with patch.object(
            grafana.prometheus, "check", return_value=None
        ) as check:
            grafana.require_prerequisites()

            check.assert_called_once_with()
            self.sleep.assert_not_called()

    def test_transient_failure_recovers(self):
        with patch.object(
            grafana.prometheus,
            "check",
            side_effect=[
                grafana.prometheus.PrometheusReadinessError("Temporary"),
                None,
            ],
        ) as check:
            grafana.require_prerequisites()

            self.assertEqual(check.call_count, 2)
            self.sleep.assert_called_once_with(2)

    def test_persistent_failure_stops(self):
        with patch.object(
            grafana.prometheus,
            "check",
            side_effect=grafana.prometheus.PrometheusReadinessError(
                "Temporary"
            ),
        ) as check:
            with self.assertRaises(grafana.GrafanaBootstrapError):
                grafana.require_prerequisites()

            self.assertEqual(check.call_count, 3)
            self.assertEqual(self.sleep.call_count, 2)

    def test_shell_prerequisite_entry_point_retries_readiness_only(self):
        with patch.object(grafana.prometheus, "check", side_effect=[
            grafana.prometheus.PrometheusReadinessError("temporary"), None,
        ]) as check:
            self.assertEqual(grafana.prometheus.main(["check-ready"]), 0)
        self.assertEqual(check.call_count, 2)

        with patch.object(grafana.prometheus, "check", side_effect=
                          grafana.prometheus.PrometheusBootstrapError("identity")) as check:
            self.assertEqual(grafana.prometheus.main(["check-ready"]), 1)
        check.assert_called_once_with()

    def test_resources_failure_blocks_prometheus(self):
        self.scan.return_value = {"resources": False}

        with patch.object(
            grafana.prometheus, "check"
        ) as check:
            with self.assertRaises(grafana.GrafanaBootstrapError):
                grafana.require_prerequisites()

            check.assert_not_called()

    def test_agent_failure_blocks_prometheus(self):
        self.agent.return_value = 1

        with patch.object(
            grafana.prometheus, "check"
        ) as check:
            with self.assertRaises(grafana.GrafanaBootstrapError):
                grafana.require_prerequisites()

            check.assert_not_called()


    def test_security_failure_is_not_retried(self):
        with patch.object(
            grafana.prometheus,
            "check",
            side_effect=grafana.prometheus.PrometheusBootstrapError(
                "Untrusted identity"
            ),
        ) as check:
            with self.assertRaises(
                grafana.prometheus.PrometheusBootstrapError
            ):
                grafana.require_prerequisites()

            check.assert_called_once_with()
            self.sleep.assert_not_called()

    def test_resource_failure_is_not_retried(self):
        with patch.object(
            grafana.prometheus,
            "check",
            side_effect=grafana.resources.ResourceError(
                "Invalid resource"
            ),
        ) as check:
            with self.assertRaises(grafana.resources.ResourceError):
                grafana.require_prerequisites()

            check.assert_called_once_with()
            self.sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
