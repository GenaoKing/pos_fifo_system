"""Pruebas sin Django para el empaquetador Windows de C06.1."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'scripts' / 'c06_construir_paquete.py'
SPEC = importlib.util.spec_from_file_location('c06_construir_paquete', SCRIPT)
assert SPEC and SPEC.loader
c06 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(c06)


class C06ConstruirPaqueteTests(unittest.TestCase):
    def test_zip_is_reproducible_when_file_mtime_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'pos_fifo_system'
            root.mkdir()
            source = root / 'apps' / 'ejemplo.txt'
            source.parent.mkdir()
            source.write_text('contenido estable\n', encoding='utf-8')

            first = Path(tmp) / 'primero.zip'
            second = Path(tmp) / 'segundo.zip'
            c06.zip_de(root, first, 1_700_000_000)
            source.touch()
            c06.zip_de(root, second, 1_700_000_000)

            self.assertEqual(
                hashlib.sha256(first.read_bytes()).hexdigest(),
                hashlib.sha256(second.read_bytes()).hexdigest(),
            )

    def test_safe_tar_rejects_path_outside_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'malicioso.tar'
            with tarfile.open(archive, 'w') as output:
                member = tarfile.TarInfo('../fuera.txt')
                payload = b'no debe escribirse fuera'
                member.size = len(payload)
                output.addfile(member, io.BytesIO(payload))

            with self.assertRaisesRegex(ValueError, 'fuera del staging'):
                c06.extraer_tar_seguro(archive, root / 'staging')
            self.assertFalse((root / 'fuera.txt').exists())

    def test_wheelhouse_invalid_fails_before_copying(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wheelhouse = root / 'wheelhouse'
            wheelhouse.mkdir()
            requirements = root / 'requirements.txt'
            requirements.write_text('django==5.2.17\n', encoding='utf-8')
            package = root / 'paquete'
            package.mkdir()

            with patch.object(c06.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
                with self.assertRaisesRegex(RuntimeError, 'no satisface requirements.txt'):
                    c06.copiar_wheelhouse_verificado(wheelhouse, requirements, package)
            self.assertFalse((package / 'wheelhouse').exists())


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
