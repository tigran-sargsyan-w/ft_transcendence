"""Regression tests for trusted Grafana HTTPS verification."""

import json
import unittest
from unittest.mock import patch

import bootstrap_grafana_https as https


class GrafanaHTTPSTests(unittest.TestCase):

    def responses(self):
        return [
            (308, b"", https.BASE + "/grafana/"),
            (200, b"<html>Grafana" + b"x" * 12000 + b"</html>", ""),
            (200, json.dumps({
                "database": "ok",
                "version": https.EXPECTED_VERSION,
            }).encode(), ""),
            (401, b"", ""),
            (403, b"", ""),
            (403, b"", ""),
        ]

    def test_verified_routes(self):
        with patch.object(
            https, "request", side_effect=self.responses(),
        ) as request:
            https.verify_routes()

        self.assertEqual(request.call_count, 6)

    def test_wrong_redirect(self):
        responses = self.responses()
        responses[0] = (308, b"", "https://example.test/")

        with patch.object(https, "request", side_effect=responses):
            with self.assertRaises(https.GrafanaHTTPSBootstrapError):
                https.verify_routes()

    def test_incomplete_html(self):
        responses = self.responses()
        responses[1] = (200, b"Grafana" + b"x" * 12000, "")

        with patch.object(https, "request", side_effect=responses):
            with self.assertRaises(https.GrafanaHTTPSBootstrapError):
                https.verify_routes()

    def test_login_unavailable(self):
        responses = self.responses()
        responses[1] = (502, b"", "")

        with patch.object(https, "request", side_effect=responses):
            with self.assertRaises(https.GrafanaHTTPSBootstrapError):
                https.verify_routes()

    def test_invalid_health_json(self):
        responses = self.responses()
        responses[2] = (200, b"invalid", "")

        with patch.object(https, "request", side_effect=responses):
            with self.assertRaises(https.GrafanaHTTPSBootstrapError):
                https.verify_routes()

    def test_database_unhealthy(self):
        responses = self.responses()
        responses[2] = (
            200, b'{"database":"failed","version":"13.3.1"}', "",
        )

        with patch.object(https, "request", side_effect=responses):
            with self.assertRaises(https.GrafanaHTTPSBootstrapError):
                https.verify_routes()

    def test_wrong_version(self):
        responses = self.responses()
        responses[2] = (
            200, b'{"database":"ok","version":"unexpected"}', "",
        )

        with patch.object(https, "request", side_effect=responses):
            with self.assertRaises(https.GrafanaHTTPSBootstrapError):
                https.verify_routes()

    def test_anonymous_access_rejected(self):
        responses = self.responses()
        responses[3] = (200, b"", "")

        with patch.object(https, "request", side_effect=responses):
            with self.assertRaises(https.GrafanaHTTPSBootstrapError):
                https.verify_routes()

    def test_waf_login_blocking_required(self):
        responses = self.responses()
        responses[4] = (200, b"", "")

        with patch.object(https, "request", side_effect=responses):
            with self.assertRaises(https.GrafanaHTTPSBootstrapError):
                https.verify_routes()

    def test_waf_api_blocking_required(self):
        responses = self.responses()
        responses[5] = (200, b"", "")

        with patch.object(https, "request", side_effect=responses):
            with self.assertRaises(https.GrafanaHTTPSBootstrapError):
                https.verify_routes()

    def test_request_requires_tls_verification(self):
        with patch.object(
            https.nginx, "run",
            return_value=b'{"database":"ok"}\n200\n',
        ) as run:
            status, _, _ = https.request("/grafana/api/health")

        command = run.call_args.args[0]

        self.assertEqual(status, 200)
        self.assertEqual(command[:2], ["curl", "-q"])
        self.assertNotIn("-k", command)
        self.assertNotIn("--insecure", command)
        self.assertIn("--noproxy", command)
        self.assertIn("--resolve", command)

    def test_request_invalid_status(self):
        with patch.object(
            https.nginx, "run", return_value=b"body\ninvalid\n",
        ):
            with self.assertRaises(https.GrafanaHTTPSBootstrapError):
                https.request("/grafana/login")

    def test_request_missing_status(self):
        with patch.object(
            https.nginx, "run", return_value=b"body",
        ):
            with self.assertRaises(https.GrafanaHTTPSBootstrapError):
                https.request("/grafana/login")

    def test_gateway_missing(self):
        with patch.object(https.nginx, "container", return_value=None):
            with self.assertRaises(https.GrafanaHTTPSBootstrapError):
                https.verify_gateway()

    def test_waf_disabled(self):
        item = {"Config": {"Env": ["MODSEC_RULE_ENGINE=Off"]}}

        with (
            patch.object(https.nginx, "container", return_value=item),
            patch.object(https.nginx, "verify_identity", return_value=item),
            patch.object(https.nginx, "running", return_value=True),
        ):
            with self.assertRaises(https.GrafanaHTTPSBootstrapError):
                https.verify_gateway()

    def test_stopped_gateway(self):
        item = {}

        with (
            patch.object(https.nginx, "container", return_value=item),
            patch.object(https.nginx, "verify_identity", return_value=item),
            patch.object(https.nginx, "running", return_value=False),
        ):
            with self.assertRaises(https.GrafanaHTTPSBootstrapError):
                https.verify_gateway()

    def test_plan_is_read_only(self):
        with patch.object(https, "check") as check:
            self.assertEqual(https.main(["plan"]), 0)

        check.assert_not_called()

    def test_check_dispatch(self):
        with patch.object(https, "check") as check:
            self.assertEqual(https.main(["check"]), 0)

        check.assert_called_once_with()

    def test_check_failure(self):
        with patch.object(
            https, "check",
            side_effect=https.GrafanaHTTPSBootstrapError("test failure"),
        ):
            self.assertEqual(https.main(["check"]), 1)

    def test_check_order(self):
        calls = []

        with (
            patch.object(
                https, "verify_gateway",
                side_effect=lambda: calls.append("gateway"),
            ),
            patch.object(
                https, "verify_routes",
                side_effect=lambda: calls.append("routes"),
            ),
        ):
            https.check()

        self.assertEqual(calls, ["gateway", "routes"])


if __name__ == "__main__":
    unittest.main()
