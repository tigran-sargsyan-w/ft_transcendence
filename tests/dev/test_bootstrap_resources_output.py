"""Offline tests: quiet inventory must preserve resource validation."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts.dev import bootstrap_resources as resources


class ResourceOutputTests(unittest.TestCase):

    def run_scan(self, *, missing=None, detailed=False):
        validated = []

        def inspect(kind, name):
            if name == missing:
                return None
            return {"Name": name}

        def validate(kind):
            def check(name, data):
                validated.append((kind, name, data["Name"]))
            return check

        output = io.StringIO()

        with (
            patch.object(resources, "inspect", side_effect=inspect),
            patch.object(
                resources, "validate_existing_network",
                side_effect=validate("network"),
            ),
            patch.object(
                resources, "validate_existing_volume",
                side_effect=validate("volume"),
            ),
            redirect_stdout(output),
        ):
            inventory = resources.scan(detailed=detailed)

        return inventory, validated, output.getvalue()

    def test_quiet_scan_still_validates_every_resource(self):
        inventory, validated, output = self.run_scan()

        self.assertTrue(all(inventory.values()))
        self.assertEqual(len(validated), len(inventory))
        self.assertEqual(output, "")

    def test_missing_resource_is_not_accepted(self):
        name = next(iter(resources.NETWORKS))
        inventory, validated, output = self.run_scan(missing=name)

        self.assertFalse(inventory[("network", name)])
        self.assertEqual(len(validated), len(inventory) - 1)
        self.assertEqual(output, "")

    def test_detailed_scan_preserves_inventory_output(self):
        inventory, validated, output = self.run_scan(detailed=True)

        self.assertTrue(all(inventory.values()))
        self.assertEqual(len(validated), len(inventory))
        self.assertIn("KEEP: network", output)
        self.assertIn("KEEP: volume", output)

    def test_invalid_resource_still_fails(self):
        with (
            patch.object(
                resources, "inspect",
                return_value={"Name": "invalid"},
            ),
            patch.object(
                resources, "validate_existing_network",
                side_effect=resources.ResourceError(
                    "Untrusted network"
                ),
            ),
            redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(resources.ResourceError):
                resources.scan()


if __name__ == "__main__":
    unittest.main()
