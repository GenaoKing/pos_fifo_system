"""Pruebas sin Django ni Docker para el manifiesto de release."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "release_manifest.py"
SPEC = importlib.util.spec_from_file_location("release_manifest", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
release_manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_manifest)


class ReleaseManifestTests(unittest.TestCase):
    source_sha = "a" * 40
    base_digest = "b" * 64

    def build_root(self, root: Path) -> None:
        for relative in release_manifest.INPUT_PATHS:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("input==1.0\n", encoding="utf-8")

        for relative in release_manifest.LOCK_PATHS:
            path = root / relative
            path.write_text(
                "# pip-compile --generate-hashes\n"
                "Django==5.2.17 \\\n"
                "    --hash=sha256:" + "c" * 64 + "\n",
                encoding="utf-8",
            )

        (root / ".dockerignore").write_text(".git/\n", encoding="utf-8")
        (root / "Dockerfile").write_text(
            f"FROM python:3.12-slim-bookworm@sha256:{self.base_digest}\n"
            "RUN python -m pip install --require-hashes -r requirements_cloud.txt\n",
            encoding="utf-8",
        )
        migration = root / "apps" / "sample" / "migrations" / "0001_initial.py"
        migration.parent.mkdir(parents=True, exist_ok=True)
        migration.write_text(
            "from django.db import migrations\n\n"
            "class Migration(migrations.Migration):\n"
            "    dependencies = [('auth', '0001_initial')]\n",
            encoding="utf-8",
        )

    def test_write_and_verify_match_source_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build_root(root)
            manifest = release_manifest.build_manifest(root, self.source_sha)
            target = root / "manifest.json"
            release_manifest.write_manifest(target, manifest)

            self.assertEqual(
                [],
                release_manifest.verify_manifest(root, target, self.source_sha, False),
            )
            self.assertEqual(1, manifest["migrations"]["count"])
            self.assertEqual(
                ["('auth', '0001_initial')"],
                manifest["migrations"]["files"][0]["declared_dependencies"],
            )

    def test_verify_rejects_changed_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build_root(root)
            target = root / "manifest.json"
            release_manifest.write_manifest(
                target,
                release_manifest.build_manifest(root, self.source_sha),
            )
            migration = root / "apps" / "sample" / "migrations" / "0001_initial.py"
            migration.write_text("# changed after manifest\n", encoding="utf-8")

            errors = release_manifest.verify_manifest(root, target, self.source_sha, False)
            self.assertTrue(any("no coincide exactamente" in error for error in errors))

    def test_promotable_requires_registry_digest_not_local_image_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build_root(root)
            digest = "sha256:" + "d" * 64
            target = root / "manifest.json"
            release_manifest.write_manifest(
                target,
                release_manifest.build_manifest(
                    root,
                    self.source_sha,
                    f"registry.example/pos-fifo-backend@{digest}",
                    digest,
                ),
            )

            self.assertEqual(
                [],
                release_manifest.verify_manifest(root, target, self.source_sha, True),
            )


if __name__ == "__main__":
    unittest.main()
