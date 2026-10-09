
import unittest

import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts.dev import linux_hosts

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

    
    def test_cleanup_removes_managed_entry(self):
        original = (
            "127.0.0.1 localhost\n"
            "127.0.0.1 transcendence.test "
            "# ft_transcendence:managed\n"
        )

        with tempfile.TemporaryDirectory() as directory:
            hosts = Path(directory) / "hosts"
            hosts.write_text(original)

            with (
                patch.object(linux_hosts, "HOSTS_FILE", hosts),
                patch.object(
                    linux_hosts.os, "geteuid", return_value=0
                ),
            ):
                result = linux_hosts.remove_mapping(
                    "transcendence.test"
                )

            self.assertEqual(result, 0)
            self.assertEqual(
                hosts.read_text(),
                "127.0.0.1 localhost\n",
            )

    def test_cleanup_preserves_unmanaged_entries(self):
        original = (
            "127.0.0.1 localhost\n"
            "127.0.0.1 transcendence.test\n"
            "192.168.1.10 another-project.test\n"
        )

        with tempfile.TemporaryDirectory() as directory:
            hosts = Path(directory) / "hosts"
            hosts.write_text(original)

            with (
                patch.object(linux_hosts, "HOSTS_FILE", hosts),
                patch.object(
                    linux_hosts.os, "geteuid", return_value=0
                ),
            ):
                result = linux_hosts.remove_mapping(
                    "transcendence.test"
                )

            self.assertEqual(result, 0)
            self.assertEqual(hosts.read_text(), original)

    def test_cleanup_is_idempotent(self):
        original = "127.0.0.1 localhost\n"

        with tempfile.TemporaryDirectory() as directory:
            hosts = Path(directory) / "hosts"
            hosts.write_text(original)

            with (
                patch.object(linux_hosts, "HOSTS_FILE", hosts),
                patch.object(
                    linux_hosts.os, "geteuid", return_value=0
                ),
            ):
                first = linux_hosts.remove_mapping(
                    "transcendence.test"
                )
                second = linux_hosts.remove_mapping(
                    "transcendence.test"
                )

            self.assertEqual(first, 0)
            self.assertEqual(second, 0)
            self.assertEqual(hosts.read_text(), original)



if __name__ == "__main__":
    unittest.main()
