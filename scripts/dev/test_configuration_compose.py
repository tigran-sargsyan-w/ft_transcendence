"""Offline coverage for the guarded public Compose environment adapters."""
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.dev import bootstrap_postgres as pg
from scripts.dev import bootstrap_resources as resources
from scripts.dev import configuration
from scripts.dev import bootstrap_postgres_roles as roles

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "configuration_preflight", ROOT / "infra/vault/scripts/dev-preflight.py"
)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


class PublicComposeEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.config = configuration.DevelopmentConfiguration(
            "example.test", 9080, 9443, 9200, 9555, 24
        )
        self.hostile = {
            "APP_ORIGIN": "https://unexpected.test",
            "DEV_DOMAIN": "unexpected.test",
            "SESSION_TTL_HOURS": "1",
            "COOKIE_SECURE": "false",
            "DATABASE_URL": "not-for-compose",
            "VAULT_TOKEN": "not-for-compose",
            "POSTGRES_PASSWORD": "not-for-compose",
            "FT_APP_COMPOSE_OVERRIDE": "not-for-compose",
        }

    def assert_public(self, env):
        for name, value in self.config.public_environment().items():
            self.assertEqual(env[name], value, name)
        for key in ("DATABASE_URL", "VAULT_TOKEN", "POSTGRES_PASSWORD",
                    "FT_APP_COMPOSE_OVERRIDE"):
            self.assertNotIn(key, env)
        self.assertEqual(env["POSTGRES_USER"], "transcendence")
        self.assertEqual(env["POSTGRES_DB"], "transcendence")

    def test_postgres_compose_env(self):
        with patch.dict(os.environ, self.hostile, clear=True), \
                patch.object(pg.configuration, "load", return_value=self.config):
            self.assert_public(pg.compose_env())
        self.assertEqual(pg.compose_args("config")[2:4], ["--env-file", "/dev/null"])

    def test_resources_model_env(self):
        with patch.dict(os.environ, self.hostile, clear=True), \
                patch.object(resources.configuration, "load", return_value=self.config), \
                patch.object(resources, "command", return_value=json.dumps({
                    "name": resources.APP_PROJECT
                }).encode()) as call:
            resources.compose_model(resources.APP_PROJECT, "compose.yml")
        self.assert_public(call.call_args.kwargs["env"])
        self.assertIn("/dev/null", call.call_args.args[0])

    def test_preflight_model_env(self):
        with patch.dict(os.environ, self.hostile, clear=True), \
                patch.object(preflight.configuration, "load", return_value=self.config), \
                patch.object(preflight, "json_command", return_value={
                    "name": preflight.APP_PROJECT
                }) as call:
            preflight.Preflight().compose_model((ROOT / "compose.yml",), preflight.APP_PROJECT)
        self.assert_public(call.call_args.kwargs["env"])
        self.assertIn("/dev/null", call.call_args.args[0])

    def test_role_and_agent_environment_uses_validated_config(self):
        with patch.dict(os.environ, self.hostile, clear=True), \
                patch.object(roles.pg.configuration, "load", return_value=self.config):
            env = roles.safe_env()
        for key, value in self.config.public_environment().items():
            self.assertEqual(env[key], value)
        for key in ("DATABASE_URL", "VAULT_TOKEN", "POSTGRES_PASSWORD", "FT_APP_COMPOSE_OVERRIDE"):
            self.assertNotIn(key, env)
        self.assertEqual(env["FT_EXISTING_RUNTIME_ONLY"], "1")

    def test_invalid_config_refused_before_compose(self):
        error = configuration.ConfigurationError("Invalid public configuration")
        with patch.object(pg.configuration, "load", side_effect=error):
            with self.assertRaisesRegex(pg.PostgresBootstrapError, "Invalid public configuration"):
                pg.compose_env()
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(resources.configuration, "load", side_effect=error), \
                patch.object(resources, "command") as call:
            with self.assertRaisesRegex(resources.ResourceError, "Invalid public configuration"):
                resources.compose_model(resources.APP_PROJECT, "compose.yml")
            call.assert_not_called()
        with patch.object(preflight.configuration, "load", side_effect=error), \
                patch.object(preflight, "json_command") as call:
            with self.assertRaisesRegex(preflight.PreflightError, "Invalid public configuration"):
                preflight.Preflight().compose_model((ROOT / "compose.yml",), preflight.APP_PROJECT)
            call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
