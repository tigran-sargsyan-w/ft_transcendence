"""Offline regression tests for monitoring bootstrap check ordering."""

import io
import sys
import unittest

from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path("scripts/dev").resolve()))

import bootstrap_prometheus as prometheus
import bootstrap_grafana as grafana


class PrometheusCheckTests(unittest.TestCase):

    def test_missing_container_fails_without_dependencies(self):
        with (
            patch.object(prometheus, "container", return_value=None),
            patch.object(prometheus, "require_prerequisites") as deps,
            patch.object(prometheus, "probe_http") as probe,
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(prometheus.main(["check"]), 1)
            deps.assert_not_called()
            probe.assert_not_called()

    def test_untrusted_identity_fails_before_dependencies(self):
        with (
            patch.object(prometheus, "container", return_value={}),
            patch.object(prometheus, "require_prerequisites") as deps,
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(prometheus.main(["check"]), 1)
            deps.assert_not_called()

    def test_existing_container_keeps_all_checks(self):
        item = {"container": "existing"}
        tracker = Mock()

        with (
            patch.object(prometheus, "container", return_value=item) as container,
            patch.object(prometheus, "verify_identity") as identity,
            patch.object(prometheus, "require_prerequisites") as deps,
            patch.object(prometheus, "running", return_value=True) as running,
            patch.object(prometheus, "probe_http") as probe,
            redirect_stdout(io.StringIO()),
        ):
            for name, mock in (
                ("container", container),
                ("identity", identity),
                ("dependencies", deps),
                ("running", running),
                ("probe", probe),
            ):
                tracker.attach_mock(mock, name)

            self.assertEqual(prometheus.main(["check"]), 0)

        self.assertEqual(tracker.mock_calls, [
            call.container(),
            call.identity(item),
            call.dependencies(),
            call.running(item),
            call.probe(),
        ])

    def test_dependency_failure_blocks_readiness(self):
        with (
            patch.object(prometheus, "container", return_value={}),
            patch.object(prometheus, "verify_identity"),
            patch.object(
                prometheus,
                "require_prerequisites",
                side_effect=prometheus.PrometheusBootstrapError(
                    "Dependency unavailable"
                ),
            ),
            patch.object(prometheus, "probe_http") as probe,
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(prometheus.main(["check"]), 1)
            probe.assert_not_called()


class GrafanaCheckTests(unittest.TestCase):

    def test_missing_container_fails_without_dependencies(self):
        with (
            patch.object(grafana, "container", return_value=None),
            patch.object(grafana, "require_prerequisites") as deps,
        ):
            with self.assertRaises(grafana.GrafanaBootstrapError):
                grafana.check()
            deps.assert_not_called()

    def test_untrusted_identity_fails_before_dependencies(self):
        with (
            patch.object(grafana, "container", return_value={}),
            patch.object(grafana, "require_prerequisites") as deps,
        ):
            with self.assertRaises(grafana.GrafanaBootstrapError):
                grafana.check()
            deps.assert_not_called()

    def test_existing_container_keeps_all_checks(self):
        raw = {"container": "existing"}
        trusted = {"container": "verified"}
        tracker = Mock()

        with (
            patch.object(grafana, "container", return_value=raw) as container,
            patch.object(
                grafana, "verify_identity", return_value=trusted
            ) as identity,
            patch.object(grafana, "require_prerequisites") as deps,
            patch.object(grafana, "running", return_value=True) as running,
            patch.object(grafana, "probe_http") as probe,
        ):
            for name, mock in (
                ("container", container),
                ("identity", identity),
                ("dependencies", deps),
                ("running", running),
                ("probe", probe),
            ):
                tracker.attach_mock(mock, name)

            grafana.check()

        self.assertEqual(tracker.mock_calls, [
            call.container(),
            call.identity(raw),
            call.dependencies(),
            call.running(trusted),
            call.probe(),
        ])

    def test_dependency_failure_blocks_readiness(self):
        with (
            patch.object(grafana, "container", return_value={}),
            patch.object(grafana, "verify_identity", return_value={}),
            patch.object(
                grafana,
                "require_prerequisites",
                side_effect=grafana.GrafanaBootstrapError(
                    "Dependency unavailable"
                ),
            ),
            patch.object(grafana, "probe_http") as probe,
        ):
            with self.assertRaises(grafana.GrafanaBootstrapError):
                grafana.check()
            probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
