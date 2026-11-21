"""Offline Phase 6E contract tests for the documented Makefile entry points.

Dry-run Make targets only: no Docker, Vault, database, or host mutation.
"""

from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


class MakefileStartupContractTests(unittest.TestCase):
    def dry_run(self, *args):
        result = subprocess.run(
            ["make", "--no-print-directory", "-n", *args],
            cwd=ROOT, capture_output=True, text=True, check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_default_is_guarded_start(self):
        self.assertEqual(self.dry_run(), "./scripts/bootstrap.sh --start")

    def test_up_and_start_are_guarded(self):
        for target in ("up", "start"):
            with self.subTest(target=target):
                self.assertEqual(self.dry_run(target), "./scripts/bootstrap.sh --start")

    def test_initialize_is_explicit(self):
        self.assertEqual(self.dry_run("initialize"), "./scripts/bootstrap.sh --initialize")

    def test_recover_is_explicit(self):
        self.assertEqual(self.dry_run("recover"), "./scripts/bootstrap.sh --recover")

    def test_health_is_read_only(self):
        self.assertEqual(self.dry_run("health"), "./scripts/bootstrap.sh --health-report")
        self.assertEqual(self.dry_run("check-all"), "./scripts/bootstrap.sh --check-all")
        self.assertEqual(self.dry_run("plan"), "./scripts/bootstrap.sh --plan")

    def test_acceptance_is_opt_in(self):
        self.assertEqual(
            self.dry_run("acceptance"),
            "python3 scripts/dev/verify_startup_idempotency.py --runs 3",
        )

    def test_documented_links_and_commands(self):
        readme = (ROOT / "README.md").read_text()
        guide = (ROOT / "docs/dev-getting-started.md").read_text()
        for fragment in ("make up", "make initialize", "make recover", "make health"):
            self.assertIn(fragment, readme)
            self.assertIn(fragment, guide)
        for path in ("docs/dev-reboot-recovery.md", "docs/dev-health-report.md",
                     "docs/dev-startup-idempotency.md", "docs/dev-final-acceptance.md"):
            self.assertIn(path, readme)
        self.assertIn("not unattended Auto Unseal", readme)


if __name__ == "__main__":
    unittest.main()
