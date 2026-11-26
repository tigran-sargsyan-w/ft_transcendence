"""Offline checks; no host, Docker or certificate store mutations."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

if __package__:
    from . import configuration as cfg
else:
    import configuration as cfg


class DevelopmentConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "development.env"
        self.sample = (
            "DEV_DOMAIN=transcendence.test\nDEV_HTTP_PORT=8080\n"
            "DEV_HTTPS_PORT=8443\nDEV_VAULT_UI_PORT=8200\n"
            "SESSION_TTL_HOURS=168\nCOOKIE_SECURE=true\n"
        )

    def load(self, text=None):
        self.path.write_text(self.sample if text is None else text, encoding="utf-8")
        return cfg.load(self.path)

    def test_repository_config_valid(self):
        self.assertIsInstance(cfg.load(), cfg.DevelopmentConfiguration)

    def test_sample_defaults(self):
        config = self.load()
        self.assertEqual(config.app_origin, "https://transcendence.test:8443")
        self.assertEqual(config.http_origin, "http://transcendence.test:8080")
        self.assertEqual(config.vault_ui_url, "https://127.0.0.1:8200/ui/")
        self.assertEqual(config.session_ttl_hours, 168)

    def test_localhost_and_changed_ports(self):
        config = self.load(self.sample.replace("transcendence.test", "localhost")
                           .replace("=8443", "=9443").replace("=8080", "=9080")
                           .replace("=8200", "=9200"))
        self.assertEqual(config.app_origin, "https://localhost:9443")
        self.assertEqual(config.http_origin, "http://localhost:9080")
        self.assertEqual(config.health_url, "https://localhost:9443/api/v1/health")
        self.assertEqual(config.grafana_root_url, "https://localhost:9443/grafana/")
        self.assertEqual(config.vault_ui_url, "https://127.0.0.1:9200/ui/")

    def test_vault_host_independent(self):
        config = self.load(self.sample.replace("transcendence.test", "example.test"))
        self.assertEqual(config.vault_ui_url, "https://127.0.0.1:8200/ui/")

    def test_environment_does_not_override(self):
        with patch.dict(os.environ, {"DEV_DOMAIN": "evil.test", "APP_ORIGIN": "https://evil.test",
                                     "COOKIE_SECURE": "false", "POSTGRES_PASSWORD": "secret"}):
            config = self.load()
            public = config.public_environment()
        self.assertEqual(public["APP_ORIGIN"], "https://transcendence.test:8443")
        self.assertEqual(public["COOKIE_SECURE"], "true")
        self.assertNotIn("POSTGRES_PASSWORD", public)
        self.assertEqual(set(public), cfg.KEYS | {"APP_ORIGIN", "GRAFANA_ROOT_URL"})

    def test_compose_adapter_uses_validated_public_environment(self):
        config = self.load(self.sample.replace("transcendence.test", "example.test"))
        with patch.dict(os.environ, {"APP_ORIGIN": "https://wrong.test", "COOKIE_SECURE": "false",
                                    "FT_DEV_TLS_DIR": "/tmp/test-tls"}, clear=True), \
                patch.object(cfg, "load", return_value=config), \
                patch.object(cfg.os, "execvpe") as execute:
            cfg.compose_command(["-f", "compose.yml", "config"])
        command, args, env = execute.call_args.args
        self.assertEqual(command, "docker")
        self.assertEqual(args, ["docker", "compose", "--env-file", "/dev/null",
                                "-f", "compose.yml", "config"])
        self.assertEqual(env["APP_ORIGIN"], "https://example.test:8443")
        self.assertEqual(env["COOKIE_SECURE"], "true")
        self.assertEqual(env["FT_DEV_TLS_DIR"], "/tmp/test-tls")

    def test_compose_adapter_invalid_config_never_executes(self):
        with patch.object(cfg, "load", side_effect=cfg.ConfigurationError("Invalid configuration")), \
                patch.object(cfg.os, "execvpe") as execute:
            with self.assertRaises(cfg.ConfigurationError):
                cfg.compose_command(["config"])
            execute.assert_not_called()

    def test_invalid_domains(self):
        for value in ("", "https://localhost", "localhost:8443", "*.test", "a..test",
                      "-a.test", "a-.test", "a.test/path", "a.test;", "a.test\nfoo=bar",
                      "127.0.0.1", "example.com", "LOCALHOST", "a.test.", "a test", "$HOME", "é.test"):
            with self.subTest(value=value), self.assertRaises(cfg.ConfigurationError):
                self.load(self.sample.replace("transcendence.test", value))

    def test_invalid_ports(self):
        for value in ("0", "80", "65536", "-1", "08", "8.5", " 8443", "8443 ", "${PORT}"):
            with self.subTest(value=value), self.assertRaises(cfg.ConfigurationError):
                self.load(self.sample.replace("=8443", "=" + value))

    def test_duplicate_ports(self):
        with self.assertRaises(cfg.ConfigurationError):
            self.load(self.sample.replace("=8443", "=8080"))

    def test_invalid_session_lifetime(self):
        for value in ("0", "-1", "NaN", "1.5", "99999999999999999", "9999999999"):
            with self.subTest(value=value), self.assertRaises(cfg.ConfigurationError):
                self.load(self.sample.replace("=168", "=" + value))

    def test_insecure_cookie_refused(self):
        with self.assertRaises(cfg.ConfigurationError):
            self.load(self.sample.replace("COOKIE_SECURE=true", "COOKIE_SECURE=false"))

    def test_unknown_key_not_logged(self):
        with self.assertRaises(cfg.ConfigurationError) as ctx:
            self.load(self.sample + "POSTGRES_PASSWORD=sensitive-marker\n")
        self.assertNotIn("sensitive-marker", str(ctx.exception))

    def test_duplicate_key_refused(self):
        with self.assertRaises(cfg.ConfigurationError):
            self.load(self.sample + "DEV_DOMAIN=other.test\n")

    def test_missing_key_refused(self):
        with self.assertRaises(cfg.ConfigurationError):
            self.load(self.sample.replace("COOKIE_SECURE=true\n", ""))

    def test_shell_syntax_not_evaluated(self):
        with self.assertRaises(cfg.ConfigurationError):
            self.load(self.sample.replace("transcendence.test", "$(touch should-not-exist)"))

    def test_missing_file(self):
        with self.assertRaises(cfg.ConfigurationError):
            cfg.load(self.path)

    def test_invalid_encoding(self):
        self.path.write_bytes(b"\xff")
        with self.assertRaises(cfg.ConfigurationError):
            cfg.load(self.path)

    def test_comment_and_blank_lines(self):
        self.assertEqual(self.load("\n  # comment\n" + self.sample), self.load())


if __name__ == "__main__":
    unittest.main()
