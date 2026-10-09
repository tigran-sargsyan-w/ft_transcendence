
import unittest

from scripts.dev.hosts_editor import (
    HostsConflictError,
    ensure_mapping,
    find_addresses,
    remove_managed_mapping,
)


DOMAIN = "transcendence.test"


class HostsEditorTests(unittest.TestCase):

    def test_add_missing_domain(self):
        result, changed = ensure_mapping(
            "127.0.0.1 localhost\n", DOMAIN
        )

        self.assertTrue(changed)
        self.assertIn(
            "127.0.0.1\ttranscendence.test",
            result,
        )

    def test_idempotency(self):
        original = "127.0.0.1 localhost\n"

        first, _ = ensure_mapping(original, DOMAIN)
        second, changed = ensure_mapping(first, DOMAIN)

        self.assertFalse(changed)
        self.assertEqual(first, second)

    def test_existing_alias(self):
        original = "127.0.0.1 localhost transcendence.test\n"

        result, changed = ensure_mapping(original, DOMAIN)

        self.assertFalse(changed)
        self.assertEqual(result, original)

    def test_conflicting_address(self):
        original = "192.168.1.10 transcendence.test\n"

        with self.assertRaises(HostsConflictError):
            ensure_mapping(original, DOMAIN)

    def test_windows_line_endings(self):
        original = "127.0.0.1 localhost\r\n"

        result, _ = ensure_mapping(original, DOMAIN)

        self.assertTrue(result.endswith(
            "127.0.0.1\ttranscendence.test\t"
            "# ft_transcendence:managed\r\n"
        ))

    def test_remove_only_managed_entry(self):
        original = "127.0.0.1 localhost\n"

        modified, _ = ensure_mapping(original, DOMAIN)
        restored, changed = remove_managed_mapping(
            modified, DOMAIN
        )

        self.assertTrue(changed)
        self.assertEqual(restored, original)

    def test_ignores_commented_entries(self):
        content = "# 192.168.1.10 transcendence.test\n"

        self.assertEqual(
            find_addresses(content, DOMAIN),
            set(),
        )


if __name__ == "__main__":
    unittest.main()
