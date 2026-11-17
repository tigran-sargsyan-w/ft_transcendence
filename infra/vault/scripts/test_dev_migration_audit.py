import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dev_migration_audit import (
    MigrationAuditError,
    audit_migrations,
)


class MigrationAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.directory = Path(self.tmp.name)
        self.name = "20260920161343_init_user"
        path = self.directory / self.name
        path.mkdir()

        self.sql = b"CREATE TABLE example (id INT);\n"
        (path / "migration.sql").write_bytes(self.sql)
        self.checksum = hashlib.sha256(self.sql).hexdigest()

    def run_audit(self, lines):
        with patch(
            "dev_migration_audit.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [], 0, lines, ""
            ),
        ):
            return audit_migrations(self.directory)

    def test_current(self):
        row = f"{self.name}|{self.checksum}|t|f"
        self.assertEqual(self.run_audit(row), [])

    def test_pending(self):
        self.assertEqual(
            self.run_audit(""),
            [self.name],
        )

    def test_failed(self):
        row = f"{self.name}|{self.checksum}|f|f"

        with self.assertRaisesRegex(
            MigrationAuditError, "incomplete"
        ):
            self.run_audit(row)

    def test_checksum_mismatch(self):
        row = f"{self.name}|{'0' * 64}|t|f"

        with self.assertRaisesRegex(
            MigrationAuditError, "checksum"
        ):
            self.run_audit(row)

    def test_unknown_applied_migration(self):
        row = f"20260101000000_unknown|{self.checksum}|t|f"

        with self.assertRaisesRegex(
            MigrationAuditError, "missing locally"
        ):
            self.run_audit(row)

    def test_rolled_back_can_be_pending(self):
        row = f"{self.name}|{self.checksum}|f|t"
        self.assertEqual(self.run_audit(row), [self.name])


if __name__ == "__main__":
    unittest.main()
