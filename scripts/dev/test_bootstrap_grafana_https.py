"""Regression tests for trusted Grafana HTTPS verification."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bootstrap_grafana_https as https


class GrafanaHTTPSTests(unittest.TestCase):
    def setUp(self):
        mock = patch.object(https.nginx.certificates, "curl_ca_args", return_value=["--cacert", "/test/rootCA.pem"])
        mock.start()
        self.addCleanup(mock.stop)


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
        self.assertEqual(command[command.index("--cacert") + 1], "/test/rootCA.pem")

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

    def verify_policy_fixture(self, runtime_rules):
        configuration = (
            b"ctl:auditLogParts=-BCDEFGHIJK\\n"
            b"error_log /var/log/nginx/error.log crit;\\n"
        )

        trusted_rules = b"# trusted PromQL rules\\n"

        item = {
            "Config": {
                "Env": ["MODSEC_RULE_ENGINE=On"],
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "nginx.conf"
            rules = Path(directory) / "promql.conf"

            config.write_bytes(configuration)
            rules.write_bytes(trusted_rules)

            with (
                patch.object(https.nginx, "CONFIG", config),
                patch.object(https.nginx, "PROMQL_RULES", rules),
                patch.object(https.nginx, "container", return_value=item),
                patch.object(https.nginx, "verify_identity"),
                patch.object(https.nginx, "running", return_value=True),
                patch.object(
                    https.nginx, "run",
                    side_effect=[configuration, runtime_rules],
                ),
            ):
                https.verify_gateway()

    def test_runtime_promql_policy_matches_source(self):
        self.verify_policy_fixture(b"# trusted PromQL rules\\n")

    def test_runtime_promql_policy_drift_rejected(self):
        with self.assertRaisesRegex(
            https.GrafanaHTTPSBootstrapError,
            "Runtime PromQL CRS rules differ",
        ):
            self.verify_policy_fixture(b"# modified rules\\n")

    def test_empty_runtime_promql_policy_rejected(self):
        with self.assertRaises(
            https.GrafanaHTTPSBootstrapError,
        ):
            self.verify_policy_fixture(b"")

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

    def test_ready_retries_only_temporary_upstream_response(self):
        with patch.object(https, "check", side_effect=[
            https.GrafanaHTTPSReadinessError("temporary"), None,
        ]) as check, patch.object(https.time, "sleep") as sleep:
            https.check_ready()
        self.assertEqual(check.call_count, 2)
        sleep.assert_called_once_with(3)

    def test_ready_does_not_retry_security_failure(self):
        with patch.object(https, "check", side_effect=https.GrafanaHTTPSBootstrapError(
            "policy mismatch")) as check, patch.object(https.time, "sleep") as sleep:
            with self.assertRaises(https.GrafanaHTTPSBootstrapError):
                https.check_ready()
        check.assert_called_once_with()
        sleep.assert_not_called()

    def test_ready_persistent_upstream_failure_refused(self):
        with patch.object(https, "check", side_effect=https.GrafanaHTTPSReadinessError(
            "temporary")) as check, patch.object(https.time, "sleep") as sleep:
            with self.assertRaises(https.GrafanaHTTPSReadinessError):
                https.check_ready()
        self.assertEqual(check.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

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
