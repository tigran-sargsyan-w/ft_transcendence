"""Verify Keyring storage command without accessing secrets."""

import subprocess
import unittest
from unittest.mock import patch

import dev_unseal_workflow as workflow


class KeyringStorageTests(unittest.TestCase):
    def test_store_uses_explicit_collection_and_stdin(self):
        share = "fake-test-share-123456789"
        result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        with patch.object(workflow, "_call", return_value=result) as call:
            workflow._store(1, "test-volume-created-at", share)

        command = call.call_args.args[0]
        kwargs = call.call_args.kwargs

        self.assertIn(
            f"--collection={workflow.KEYRING_COLLECTION}",
            command,
        )
        self.assertNotIn("--collection=login", command)
        self.assertNotIn(share, command)
        self.assertEqual(kwargs["data"], share)


if __name__ == "__main__":
    unittest.main()
