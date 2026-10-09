
import unittest

from scripts.dev.hosts_editor import HostsConflictError
from scripts.dev.linux_hosts import (
    append_text,
    validate_domain,
)


class LinuxHostsTests(unittest.TestCase):

    def test_valid_domain(self):
        self.assertEqual(
            validate_domain("Transcendence.TEST"),
            "transcendence.test",
        )

    def test_invalid_domain(self):
        for domain in (
            "../transcendence.test",
            "-invalid.test",
            "example.com",
        ):
            with self.subTest(domain=domain):
                with self.assertRaises(ValueError):
                    validate_domain(domain)

    def test_append_missing_mapping(self):
        content = "127.0.0.1 localhost\n"

        addition = append_text(
            content, "transcendence.test"
        )

        self.assertIn(
            "127.0.0.1\ttranscendence.test",
            addition,
        )

    def test_no_duplicate_mapping(self):
        content = (
            "127.0.0.1 transcendence.test\n"
        )

        self.assertEqual(
            append_text(content, "transcendence.test"),
            "",
        )

    def test_conflicting_mapping(self):
        content = (
            "192.168.1.5 transcendence.test\n"
        )

        with self.assertRaises(HostsConflictError):
            append_text(content, "transcendence.test")


if __name__ == "__main__":
    unittest.main()
