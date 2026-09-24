import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.env_loader import cargar_env_file


class CargarEnvFileTests(SimpleTestCase):
    def test_archivo_default_ausente_es_opcional(self):
        with tempfile.TemporaryDirectory() as temporal:
            with patch.dict(os.environ, {}, clear=True):
                self.assertIsNone(
                    cargar_env_file(base_dir=Path(temporal), environ=os.environ)
                )

    def test_pos_env_file_exige_ruta_absoluta(self):
        with patch.dict(
            os.environ,
            {'POS_ENV_FILE': 'deploy/env_cliente.env'},
            clear=True,
        ):
            with self.assertRaisesMessage(
                ImproperlyConfigured,
                'POS_ENV_FILE debe ser una ruta absoluta',
            ):
                cargar_env_file(base_dir=Path.cwd(), environ=os.environ)

    def test_pos_env_file_declarado_debe_existir(self):
        with tempfile.TemporaryDirectory() as temporal:
            inexistente = Path(temporal) / 'no-existe.env'
            with patch.dict(
                os.environ,
                {'POS_ENV_FILE': str(inexistente)},
                clear=True,
            ):
                with self.assertRaisesMessage(
                    ImproperlyConfigured,
                    'POS_ENV_FILE no existe o no es un archivo',
                ):
                    cargar_env_file(base_dir=Path(temporal), environ=os.environ)

    def test_entorno_del_proceso_prevalece_y_archivo_es_utf8(self):
        with tempfile.TemporaryDirectory() as temporal:
            ruta = Path(temporal) / 'cliente.env'
            ruta.write_text(
                'A01_PRECEDENCIA=archivo\nA01_UTF8=ni\u00f1o\n',
                encoding='utf-8',
            )
            entorno = {
                'POS_ENV_FILE': str(ruta),
                'A01_PRECEDENCIA': 'proceso',
            }
            with patch.dict(os.environ, entorno, clear=True):
                cargado = cargar_env_file(
                    base_dir=Path(temporal),
                    environ=os.environ,
                )
                self.assertEqual(cargado, ruta.resolve())
                self.assertEqual(os.environ['A01_PRECEDENCIA'], 'proceso')
                self.assertEqual(os.environ['A01_UTF8'], 'ni\u00f1o')
