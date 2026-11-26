"""Offline tests for the guarded first-run benchmark (never calls Docker)."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/dev/benchmark_initialize.py"
# Also support checking this downloadable test beside the downloadable script.
if not SCRIPT.is_file():
    SCRIPT = Path(__file__).with_name("benchmark_initialize.py")
spec = importlib.util.spec_from_file_location("benchmark_initialize", SCRIPT)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class BenchmarkRecorderTests(unittest.TestCase):
    def setUp(self):
        self.recorder = benchmark.Recorder(100.0)
        self.answers = []

    def feed(self, text, now=1.0):
        self.recorder.feed(text.encode(), self.answers.append, now=now)

    def test_formerly_reviewed_prompt_is_refused_even_when_split(self):
        self.feed("Download these missing Docker ")
        self.feed("images? [y/N]")
        self.assertEqual(self.answers, [b"n\n"])
        self.assertEqual(self.recorder.unexpected_confirmations, 1)
        self.assertEqual(self.recorder.approvals, [])
        self.assertIsNotNone(self.recorder.rejection)

    def test_unrecognized_question_is_refused(self):
        self.feed("Delete all Docker volumes? [y/N]")
        self.assertEqual(self.answers, [b"n\n"])
        self.assertIsNotNone(self.recorder.rejection)

    def test_unreviewed_yes_default_prompt_is_refused(self):
        self.feed("Delete sensitive data? [Y/n]")
        self.assertEqual(self.answers, [b"n\n"])

    def test_safe_child_prompt_refusal_marker_fails_benchmark(self):
        self.feed(benchmark.PROMPT_REFUSED + "\n")
        self.assertEqual(self.answers, [])
        self.assertEqual(self.recorder.unexpected_confirmations, 1)
        self.assertIsNotNone(self.recorder.rejection)

    def test_timing_and_marker_are_recorded_without_raw_output(self):
        self.feed("TIMING: stage=vault attempt=1/3 result=FAIL elapsed=0.08s\n", 1.0)
        self.feed("PREPARE: vault\n", 2.0)
        self.feed("TIMING: stage=vault attempt=1/3 result=PASS elapsed=0.20s\n", 5.0)
        self.feed(benchmark.READY_MARKER + "\n", 6.0)
        report = self.recorder.report("a" * 40, "optimized", "utc", 6.0, 0)
        self.assertEqual(len(report["checks"]), 2)
        self.assertEqual(report["preparations"]["vault"]["estimated_seconds"], 2.8)
        self.assertTrue(report["readiness_marker_seen"])
        self.assertNotIn("raw_output", report)
        self.assertNotIn("started", report["preparations"]["vault"])

    def test_success_requires_exit_marker_all_stages_and_zero_confirmations(self):
        self.recorder.ready = True
        self.assertFalse(self.recorder.successful(0))
        for index, name in enumerate(benchmark.STAGE_NAMES, 1):
            self.feed(f" => [{index:02d}/21] {name} DONE 1.0s | total {index}.0s\n")
        self.assertTrue(self.recorder.successful(0))
        self.assertFalse(self.recorder.successful(1))
        self.feed("Unexpected? [y/N]")
        self.assertFalse(self.recorder.successful(0))


class BenchmarkPreflightTests(unittest.TestCase):
    def test_existing_container_volume_network_are_all_refused(self):
        for kind, resource in (
            ("container", "transcendence-app-dev-postgres-1"),
            ("volume", "transcendence-vault-dev_vault_data"),
            ("network", "transcendence-app-dev_default"),
        ):
            with self.subTest(kind=kind):
                with self.assertRaises(benchmark.BenchmarkError):
                    benchmark.ensure_empty_project_resources({kind: [resource]})

    def test_unrelated_docker_resources_are_permitted(self):
        benchmark.ensure_empty_project_resources({
            "container": ["unrelated-container"],
            "volume": ["another_volume"],
            "network": ["bridge", "host", "none"],
        })

    def test_expected_commit_format_is_enforced_before_git(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(benchmark.BenchmarkError):
                benchmark.preflight(Path(directory), "abc123")

    def test_output_directory_cannot_be_inside_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(benchmark.BenchmarkError):
                benchmark.safe_output_directory(root, root / "reports")

    def test_output_directory_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            output = Path(directory) / "private"
            with patch.object(benchmark.os, "getuid", return_value=output.parent.stat().st_uid):
                actual = benchmark.safe_output_directory(root, output)
            self.assertEqual(actual, output)
            self.assertEqual(actual.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
