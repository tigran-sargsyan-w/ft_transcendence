"""Offline tests for safe Linux pressure diagnostics."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "diagnose_vm_pressure", Path(__file__).with_name("diagnose_vm_pressure.py")
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class VMPressureTests(unittest.TestCase):
    def test_pressure_parser(self):
        value = "some avg10=0.00 avg60=0.00 avg300=0.00 total=125000\nfull avg10=0.00 total=90000\n"
        self.assertEqual(mod.parse_pressure(value), {"some": 125000, "full": 90000})

    def test_pressure_parser_rejects_unrecognized_or_invalid_values(self):
        self.assertEqual(mod.parse_pressure("unknown total=10\nsome total=secret\n"), {})

    def test_pressure_fraction(self):
        left = {"pressure": {"io": {"some": 100000}}}
        right = {"pressure": {"io": {"some": 500000}}}
        self.assertAlmostEqual(mod.pressure_fraction(left, right, "io", "some", 2.0), 20.0)
        self.assertIsNone(mod.pressure_fraction(left, right, "memory", "full", 2.0))
        self.assertIsNone(mod.pressure_fraction(right, left, "io", "some", 2.0))

    def test_parse_meminfo(self):
        sample = "MemTotal: 2048 kB\nMemAvailable: 512 kB\nSwapTotal: 1024 kB\nSwapFree: 100 kB\nBogus: 1 kB\n"
        self.assertEqual(mod.parse_meminfo(sample), {
            "MemTotal": 2097152, "MemAvailable": 524288,
            "SwapTotal": 1048576, "SwapFree": 102400,
        })

    def test_parse_cpu(self):
        self.assertEqual(mod.parse_cpu("cpu 10 2 4 100 3 1 1 5 0 0\n"), {
            "total": 126, "iowait": 3, "steal": 5,
        })
        self.assertIsNone(mod.parse_cpu("cpu not-an-int\n"))

    def test_cpu_fraction(self):
        left = {"cpu": {"total": 100, "steal": 10, "iowait": 5}}
        right = {"cpu": {"total": 200, "steal": 12, "iowait": 25}}
        self.assertEqual(mod.cpu_fraction(left, right, "steal"), 2.0)
        self.assertEqual(mod.cpu_fraction(left, right, "iowait"), 20.0)

    def test_only_read_only_commands_exposed(self):
        self.assertEqual(set(mod.COMMANDS), {"health", "check-all"})
        for cmd in mod.COMMANDS.values():
            self.assertNotIn("up", cmd)
            self.assertNotIn("initialize", cmd)
            self.assertNotIn("recover", cmd)

    def test_failed_run_stops_early(self):
        with patch.object(mod, "read_snapshot", return_value={"pressure": {}, "mem": {}, "cpu": None}), \
             patch.object(mod, "measure", return_value=(10.0, False)) as measure, \
             patch.object(mod.Path, "is_file", return_value=True), \
             patch("builtins.print"):
            self.assertEqual(mod.main(["--command", "health", "--runs", "3"]), 1)
        measure.assert_called_once()


if __name__ == "__main__":
    unittest.main()
