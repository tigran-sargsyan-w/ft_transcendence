"""Offline acceptance tests for Vault UI gateway inventory."""

import unittest
from unittest.mock import patch

from scripts.dev import verify_startup_idempotency as acceptance


def gateway(*, running=True):
    return {
        "Id": "trusted-gateway-id",
        "Created": "creation-time",
        "RestartCount": 0,
        "State": {
            "Running": running,
            "Status": "running" if running else "exited",
            "StartedAt": "startup-time",
        },
    }


class VaultUIAcceptanceTests(unittest.TestCase):

    def setUp(self):
        self.patches = [
            patch.object(
                acceptance.ui,
                "desired_enabled",
                return_value=True,
            ),
            patch.object(
                acceptance.ui,
                "inspect",
                return_value=gateway(),
            ),
            patch.object(
                acceptance.ui,
                "verify_identity",
            ),
            patch.object(
                acceptance.ui,
                "verify_network",
            ),
            patch.object(
                acceptance,
                "names_for",
                return_value=(acceptance.ui.EDGE,),
            ),
        ]

        mocks = [item.start() for item in self.patches]

        for item in self.patches:
            self.addCleanup(item.stop)

        (
            self.enabled,
            self.inspect,
            self.identity,
            self.network,
            self.names,
        ) = mocks

    def test_enabled_running_gateway_accepted(self):
        enabled, item, edge = acceptance.vault_ui_inventory()

        self.assertTrue(enabled)
        self.assertTrue(edge)
        self.assertEqual(item["Id"], "trusted-gateway-id")
        self.identity.assert_called_once()
        self.network.assert_called_once_with(
            acceptance.ui.EDGE,
            acceptance.ui.PROJECT,
            False,
        )

    def test_disabled_stopped_gateway_accepted(self):
        self.enabled.return_value = False
        self.inspect.return_value = gateway(running=False)

        enabled, item, edge = acceptance.vault_ui_inventory()

        self.assertFalse(enabled)
        self.assertFalse(item["State"]["Running"])
        self.assertTrue(edge)

    def test_disabled_absent_gateway_accepted(self):
        self.enabled.return_value = False
        self.inspect.return_value = None
        self.names.return_value = ()

        enabled, item, edge = acceptance.vault_ui_inventory()

        self.assertFalse(enabled)
        self.assertIsNone(item)
        self.assertFalse(edge)
        self.identity.assert_not_called()
        self.network.assert_not_called()

    def test_enabled_missing_gateway_rejected(self):
        self.inspect.return_value = None

        with self.assertRaises(acceptance.AcceptanceError):
            acceptance.vault_ui_inventory()

    def test_enabled_stopped_gateway_rejected(self):
        self.inspect.return_value = gateway(running=False)

        with self.assertRaises(acceptance.AcceptanceError):
            acceptance.vault_ui_inventory()

    def test_disabled_running_gateway_rejected(self):
        self.enabled.return_value = False

        with self.assertRaises(acceptance.AcceptanceError):
            acceptance.vault_ui_inventory()

    def test_missing_edge_network_rejected(self):
        self.names.return_value = ()

        with self.assertRaises(acceptance.AcceptanceError):
            acceptance.vault_ui_inventory()

    def test_untrusted_gateway_identity_rejected(self):
        self.identity.side_effect = acceptance.ui.GatewayError(
            "Untrusted identity"
        )

        with self.assertRaises(acceptance.AcceptanceError):
            acceptance.vault_ui_inventory()

    def test_changed_operator_intent_rejected(self):
        baseline = {
            "vault_ui_enabled": True,
            "container_inventory": (),
            "volume_inventory": (),
            "network_inventory": (),
            "migration_history": "fingerprint",
            "containers": {},
            "volumes": {},
            "networks": {},
        }

        current = {
            **baseline,
            "vault_ui_enabled": False,
        }

        with self.assertRaises(acceptance.AcceptanceError):
            acceptance.compare(baseline, current)

    def test_gateway_restart_rejected(self):
        baseline = {
            "vault_ui_enabled": True,
            "container_inventory": (acceptance.ui.NAME,),
            "volume_inventory": (),
            "network_inventory": (acceptance.ui.EDGE,),
            "migration_history": "fingerprint",
            "containers": {
                acceptance.ui.NAME: (
                    "gateway-id",
                    "created",
                    0,
                    "started-before",
                    "running",
                    True,
                ),
            },
            "volumes": {},
            "networks": {
                acceptance.ui.EDGE: ("network-id", "created"),
            },
        }

        current = {
            **baseline,
            "containers": {
                acceptance.ui.NAME: (
                    "gateway-id",
                    "created",
                    1,
                    "started-after",
                    "running",
                    True,
                ),
            },
        }

        with self.assertRaises(acceptance.AcceptanceError):
            acceptance.compare(baseline, current)


if __name__ == "__main__":
    unittest.main()
