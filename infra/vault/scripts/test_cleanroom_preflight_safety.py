"""Offline tests for Clean-Room isolation guardrails (#116)."""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE = Path(__file__).resolve().parents[1] / "cleanroom/preflight.py"

spec = importlib.util.spec_from_file_location(
    "cleanroom_preflight", MODULE
)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class CleanRoomPreflightSafetyTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        root = Path(self.tmp.name)

        tls = root / "tls"
        tls.mkdir()

        for name in ("ca.crt", "vault.crt", "vault.key"):
            (tls / name).write_text("test fixture")

        config = root / "config"
        config.mkdir()

        for name in ("app.override.yml", "vault.override.yml"):
            (config / name).write_text("services: {}\n")

        self.env = {
            "FT_APP_COMPOSE_PROJECT": "ft-cr-116-app",
            "FT_CR_VAULT_PROJECT": "ft-cr-116-vault",
            "FT_CR_TLS_DIR": str(tls),
            "FT_DEV_TLS_DIR": str(tls),
            "FT_CR_OVERRIDE_DIR": str(config),
            "FT_APP_COMPOSE_OVERRIDE": str(
                config / "app.override.yml"
            ),
            "FT_CR_PG_PASSWORD": "test-only-placeholder",
        }

    def assert_rejected(self, overrides, message):
        env = dict(self.env)
        env.update(overrides)

        with patch.dict(os.environ, env, clear=True):
            with patch.object(preflight, "compose_config") as compose:
                with self.assertRaisesRegex(RuntimeError, message):
                    preflight.main()

                compose.assert_not_called()

    def test_rejects_real_application_project(self):
        self.assert_rejected(
            {"FT_APP_COMPOSE_PROJECT": "transcendence-app-dev"},
            "Unsafe clean-room project names",
        )

    def test_rejects_real_vault_project(self):
        self.assert_rejected(
            {"FT_CR_VAULT_PROJECT": "transcendence-vault-dev"},
            "Unsafe clean-room project names",
        )

    def test_rejects_identical_project_names(self):
        self.assert_rejected(
            {"FT_CR_VAULT_PROJECT": "ft-cr-116-app"},
            "Unsafe clean-room project names",
        )

    def test_rejects_unexpected_override(self):
        self.assert_rejected(
            {"FT_APP_COMPOSE_OVERRIDE": "/tmp/untrusted.yml"},
            "Unexpected application Compose override",
        )

    def test_rejects_mismatched_runtime_tls(self):
        self.assert_rejected(
            {"FT_DEV_TLS_DIR": "/tmp/other-tls"},
            "FT_DEV_TLS_DIR does not match",
        )


if __name__ == "__main__":
    unittest.main()
