"""Offline regression tests for first-install image preparation."""

import re
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import bootstrap_images as images

ROOT = Path(__file__).resolve().parents[2]


class ImageTests(unittest.TestCase):
    def test_complete_image_inventory(self):
        declared = set()

        for file in (
            ROOT / "compose.yml",
            ROOT / "infra/vault/server/compose.yml",
            ROOT / "infra/vault/ui/compose.yml",
        ):
            declared.update(re.findall(
                r"(?m)^\s*image:\s*([^\s#]+)",
                file.read_text(),
            ))

        for file in (
            "backend/Dockerfile",
            "frontend/Dockerfile",
            "services/collector/Dockerfile",
            "services/graph-engine/Dockerfile",
            "infra/docker/postgres/Dockerfile",
        ):
            declared.update(re.findall(
                r"(?m)^FROM\s+([^\s]+)",
                (ROOT / file).read_text(),
            ))

        self.assertEqual(len(images.IMAGES), 10)
        self.assertEqual(len(set(images.IMAGES)), 10)
        self.assertFalse(declared - set(images.IMAGES))

    def test_only_initialize_installs_images(self):
        shell = (ROOT / "scripts/bootstrap.sh").read_text()
        unified = (ROOT / "scripts/dev/bootstrap_unified.py").read_text()
        self.assertIn("exec python3 scripts/dev/bootstrap_unified.py initialize", shell)
        self.assertNotIn("bootstrap_images.py", shell)
        self.assertIn('str(ROOT / "scripts/dev/bootstrap_images.py"), "initialize"', unified)
        for mode in ("start", "recover", "check"):
            section = unified.split(f"def {mode}():", 1)[1].split("\ndef ", 1)[0]
            self.assertNotIn("prepare_prerequisites", section)

    def test_present_images_never_pulled(self):
        with patch.object(images, "run") as runner:
            images.apply_missing([])
            runner.assert_not_called()

    def test_no_tty_refuses_mutation(self):
        with (
            patch.object(
                images.sys.stdin, "isatty", return_value=False
            ),
            patch.object(images, "run") as runner,
        ):
            with self.assertRaises(images.ImageError):
                images.apply_missing([
                    "grafana/grafana:13.3.1",
                ])

            runner.assert_not_called()

    def test_decline_refuses_mutation(self):
        with (
            patch.object(
                images.sys.stdin, "isatty", return_value=True
            ),
            patch("builtins.input", return_value="n"),
            patch.object(images, "run") as runner,
        ):
            with self.assertRaises(images.ImageError):
                images.apply_missing([
                    "grafana/grafana:13.3.1",
                ])

            runner.assert_not_called()

    def test_pull_only_missing(self):
        image = "grafana/grafana:13.3.1"

        with (
            patch.object(
                images.sys.stdin, "isatty", return_value=True
            ),
            patch("builtins.input", return_value="y"),
            patch.object(
                images, "run",
                return_value=types.SimpleNamespace(
                    returncode=0,
                ),
            ) as runner,
            patch.object(
                images, "available",
                return_value=True,
            ) as inspect,
        ):
            images.apply_missing([image])

        runner.assert_called_once_with(
            "pull", "--quiet", image, timeout=900,
        )
        inspect.assert_called_once_with(image)

    def test_initialize_pulls_only_reviewed_missing_images_without_tty(self):
        image = "grafana/grafana:13.3.1"
        with (
            patch.object(images.sys.stdin, "isatty", return_value=False),
            patch("builtins.input", side_effect=AssertionError("unexpected prompt")),
            patch.object(images, "run", return_value=types.SimpleNamespace(
                returncode=0,
            )) as runner,
            patch.object(images, "available", return_value=True),
        ):
            images.apply_missing([image], initializing=True)
        runner.assert_called_once_with("pull", "--quiet", image, timeout=900)

    def test_initialize_rejects_unreviewed_image(self):
        with patch.object(images, "run") as runner:
            with self.assertRaises(images.ImageError):
                images.apply_missing(["unexpected:latest"], initializing=True)
            runner.assert_not_called()

    def test_image_inspection_read_only(self):
        with patch.object(
            images, "run",
            return_value=types.SimpleNamespace(
                returncode=0,
                stdout=b"sha256:012345\n",
            ),
        ) as runner:
            self.assertTrue(images.available(
                "hashicorp/vault:2.1.2"
            ))

        runner.assert_called_once_with(
            "image", "inspect",
            "--format", "{{.Id}}",
            "hashicorp/vault:2.1.2",
        )
