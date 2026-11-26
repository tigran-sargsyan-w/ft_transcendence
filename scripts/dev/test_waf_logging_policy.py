"""Static regression checks for the development WAF logging policy (#161)."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class WafLoggingPolicyTests(unittest.TestCase):
    def setUp(self):
        self.compose = (ROOT / "compose.yml").read_text()
        self.nginx = (ROOT / "infra/nginx/default.conf.template").read_text()

    def test_nginx_uses_minimal_native_audit_without_disabling_waf(self):
        service = re.search(
            r"^  nginx:\n(.*?)(?=^  [\w-]+:|\Z)",
            self.compose, re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(service)
        for setting in (
            'MODSEC_RULE_ENGINE: "On"',
            'MODSEC_AUDIT_LOG_FORMAT: "Native"',
            'MODSEC_AUDIT_LOG_PARTS: "AZ"',
            'BLOCKING_PARANOIA: "1"',
        ):
            self.assertIn("      " + setting + "\n", service.group(1))

    def test_access_log_contains_only_allowlisted_metadata(self):
        match = re.search(r"^log_format safe_main\s+([^;]+);", self.nginx,
                          re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(re.findall(r"\$[a-zA-Z_]+", match.group(1)), [
            "$remote_addr", "$time_local", "$status", "$body_bytes_sent",
            "$request_time",
        ])

    def test_both_servers_apply_safe_logging(self):
        for listener in ("8080", "8443 ssl"):
            with self.subTest(listener=listener):
                match = re.search(
                    r"^server \{\s+listen " + re.escape(listener)
                    + r";((?:\$\{[A-Z_]+\}|[^{}])+)", self.nginx, re.MULTILINE,
                )
                self.assertIsNotNone(match)
                self.assertIn("access_log /var/log/nginx/access.log safe_main;",
                              match.group(1))
                self.assertIn("error_log /var/log/nginx/error.log crit;",
                              match.group(1))

    def test_existing_scoped_waf_rules_are_retained(self):
        self.assertIn("ctl:ruleRemoveById=920420", self.nginx)
        self.assertIn("ctl:ruleRemoveById=953100", self.nginx)
        self.assertIn("ctl:auditLogParts=-BCDEFGHIJK", self.nginx)
        self.assertIn("access_log off;", self.nginx)
