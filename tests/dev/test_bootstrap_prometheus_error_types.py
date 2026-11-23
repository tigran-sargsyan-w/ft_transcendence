"""Offline tests for typed Prometheus readiness and trust failures."""

import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[2] / "scripts" / "dev"),
)

import bootstrap_prometheus as prometheus


class PrometheusErrorTypeTests(unittest.TestCase):

    def setUp(self):
        self.item = {
            "State": {
                "Running": True,
                "Status": "running",
            }
        }

        self.patches = [
            patch.object(
                prometheus, "container", return_value=self.item
            ),
            patch.object(prometheus, "verify_identity"),
            patch.object(prometheus, "require_prerequisites"),
            patch.object(prometheus, "probe_http"),
        ]

        mocks = [item.start() for item in self.patches]

        for item in self.patches:
            self.addCleanup(item.stop)

        (
            self.container,
            self.identity,
            self.prerequisites,
            self.probe,
        ) = mocks

    def test_healthy_prometheus_passes(self):
        self.assertIsNone(prometheus.check())

        self.identity.assert_called_once_with(self.item)
        self.prerequisites.assert_called_once_with()
        self.probe.assert_called_once_with()

    def test_missing_container_is_permanent_failure(self):
        self.container.return_value = None

        with self.assertRaises(
            prometheus.PrometheusBootstrapError
        ) as caught:
            prometheus.check()

        self.assertNotIsInstance(
            caught.exception,
            prometheus.PrometheusReadinessError,
        )
        self.prerequisites.assert_not_called()
        self.probe.assert_not_called()

    def test_invalid_identity_is_permanent_failure(self):
        self.identity.side_effect = (
            prometheus.PrometheusBootstrapError(
                "Unexpected Prometheus identity"
            )
        )

        with self.assertRaises(
            prometheus.PrometheusBootstrapError
        ) as caught:
            prometheus.check()

        self.assertNotIsInstance(
            caught.exception,
            prometheus.PrometheusReadinessError,
        )
        self.prerequisites.assert_not_called()
        self.probe.assert_not_called()

    def test_prerequisite_failure_is_not_readiness_error(self):
        self.prerequisites.side_effect = (
            prometheus.PrometheusBootstrapError(
                "Required dependency unavailable"
            )
        )

        with self.assertRaises(
            prometheus.PrometheusBootstrapError
        ) as caught:
            prometheus.check()

        self.assertNotIsInstance(
            caught.exception,
            prometheus.PrometheusReadinessError,
        )
        self.probe.assert_not_called()

    def test_http_probe_failure_has_readiness_type(self):
        self.probe.side_effect = (
            prometheus.support.SupportServicesError(
                "HTTP probe failed"
            )
        )

        with self.assertRaises(
            prometheus.PrometheusReadinessError
        ):
            prometheus.check()

        self.identity.assert_called_once()
        self.prerequisites.assert_called_once()
        self.probe.assert_called_once()

    def test_public_check_retains_nonzero_exit_on_probe_failure(self):
        self.probe.side_effect = (
            prometheus.support.SupportServicesError(
                "HTTP probe failed"
            )
        )

        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            result = prometheus.main(["check"])

        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
