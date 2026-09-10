"""
Tests de `deploy/preflight_actualizar.py` (C01).

Este script reemplaza al `for /f ... delims==` que `actualizar.bat` usaba
para volcar `env_cliente.env` a variables de BAT. Ese patron reinterpreta el
valor ya sustituido buscando pares `%NOMBRE%` para expandir -- el mismo
defecto de bug #9 (ver `test_migrar_env_cliente.py`), aplicado esta vez a
`DB_PASSWORD` -> `PGPASSWORD` del backup de la FASE 2, la red de seguridad de
toda la actualizacion.

Es un script fuera de `apps/`, sin dependencias del proyecto Django (corre
con el venv VIEJO de la instalacion, antes de que el paquete nuevo este
copiado), por eso se carga por ruta con `importlib` en vez de importarlo
como modulo normal.
"""
import importlib.util
import io
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase


def _cargar_script():
    ruta = Path(settings.BASE_DIR) / 'deploy' / 'preflight_actualizar.py'
    spec = importlib.util.spec_from_file_location('preflight_actualizar', ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


preflight = _cargar_script()


class CampoTests(SimpleTestCase):
    """`campo` es lo que actualizar.bat usa junto a `set /p VAR=<archivo` --
    una lectura literal que no vuelve a pasar por el parser de cmd."""

    def _escribir_env(self, tmp, contenido):
        ruta = Path(tmp) / 'env_cliente.env'
        ruta.write_text(contenido, encoding='utf-8')
        return ruta

    def _campo(self, tmp, contenido, nombre):
        ruta = self._escribir_env(tmp, contenido)
        salida = io.StringIO()
        args = preflight._construir_parser().parse_args(['campo', nombre, str(ruta)])
        with redirect_stdout(salida):
            codigo = preflight.main(['campo', nombre, str(ruta)])
        return codigo, salida.getvalue()

    def test_valor_simple(self):
        with TemporaryDirectory() as tmp:
            codigo, valor = self._campo(tmp, 'SUCURSAL_CODIGO=01\n', 'SUCURSAL_CODIGO')
        self.assertEqual(codigo, 0)
        self.assertEqual(valor, '01')

    def test_valor_con_ampersand(self):
        """Justo lo que el `for /f` viejo corrompia (bug #9)."""
        with TemporaryDirectory() as tmp:
            codigo, valor = self._campo(
                tmp, 'NEGOCIO_NOMBRE=Repuestos R&B\n', 'NEGOCIO_NOMBRE',
            )
        self.assertEqual(codigo, 0)
        self.assertEqual(valor, 'Repuestos R&B')

    def test_valor_con_porcentaje_pareado(self):
        """Exactamente el patron `%NOMBRE%` que `cmd` intentaria expandir."""
        with TemporaryDirectory() as tmp:
            codigo, valor = self._campo(
                tmp, 'NEGOCIO_NOMBRE=100% Autopartes\n', 'NEGOCIO_NOMBRE',
            )
        self.assertEqual(codigo, 0)
        self.assertEqual(valor, '100% Autopartes')

    def test_valor_con_signo_de_exclamacion(self):
        """`!` es el disparador de expansion retrasada de cmd."""
        with TemporaryDirectory() as tmp:
            codigo, valor = self._campo(tmp, 'NEGOCIO_NOMBRE=Ofertas!\n', 'NEGOCIO_NOMBRE')
        self.assertEqual(codigo, 0)
        self.assertEqual(valor, 'Ofertas!')

    def test_valor_con_espacios_y_unicode(self):
        with TemporaryDirectory() as tmp:
            codigo, valor = self._campo(
                tmp, 'NEGOCIO_NOMBRE=Ferretería Peña\n', 'NEGOCIO_NOMBRE',
            )
        self.assertEqual(codigo, 0)
        self.assertEqual(valor, 'Ferretería Peña')

    def test_comentario_no_es_un_valor(self):
        with TemporaryDirectory() as tmp:
            codigo, valor = self._campo(
                tmp, '# NEGOCIO_NOMBRE=comentado\nNEGOCIO_NOMBRE=Real\n', 'NEGOCIO_NOMBRE',
            )
        self.assertEqual(codigo, 0)
        self.assertEqual(valor, 'Real')

    def test_variable_ausente_devuelve_1_y_nada(self):
        with TemporaryDirectory() as tmp:
            codigo, valor = self._campo(tmp, 'DB_NAME=pos\n', 'NEGOCIO_NOMBRE')
        self.assertEqual(codigo, 1)
        self.assertEqual(valor, '')


class BackupTests(SimpleTestCase):
    """La contrasena viaja en el entorno del subproceso, nunca en argv ni en
    una variable de BAT que `cmd` pudiera reinterpretar."""

    def test_pg_dump_recibe_pgpassword_por_entorno_no_por_argv(self):
        with TemporaryDirectory() as tmp:
            env_path = Path(tmp) / 'env_cliente.env'
            env_path.write_text(
                'DB_NAME=pos_rp\nDB_USER=pos_user\nDB_HOST=localhost\n'
                'DB_PORT=5432\nDB_PASSWORD=cl@ve&con%especiales!\n',
                encoding='utf-8',
            )
            destino = str(Path(tmp) / 'backup.dump')

            with mock.patch.object(preflight.subprocess, 'run') as run_mock:
                run_mock.return_value = mock.Mock(returncode=0)
                codigo = preflight.main(['backup', str(env_path), destino])

        self.assertEqual(codigo, 0)
        run_mock.assert_called_once()
        argv, kwargs = run_mock.call_args
        comando = argv[0]
        self.assertNotIn('cl@ve&con%especiales!', comando)
        self.assertEqual(kwargs['env']['PGPASSWORD'], 'cl@ve&con%especiales!')
        self.assertIn('pos_rp', comando)
        self.assertIn('pos_user', comando)

    def test_faltan_variables_de_conexion_no_llama_pg_dump(self):
        with TemporaryDirectory() as tmp:
            env_path = Path(tmp) / 'env_cliente.env'
            env_path.write_text('DB_NAME=pos\n', encoding='utf-8')

            with mock.patch.object(preflight.subprocess, 'run') as run_mock:
                salida = io.StringIO()
                with redirect_stderr(salida):
                    codigo = preflight.main(['backup', str(env_path), str(Path(tmp) / 'x.dump')])

        self.assertEqual(codigo, 1)
        run_mock.assert_not_called()
        self.assertIn('DB_USER', salida.getvalue())

    def test_pg_dump_ausente_del_path_falla_claro(self):
        with TemporaryDirectory() as tmp:
            env_path = Path(tmp) / 'env_cliente.env'
            env_path.write_text(
                'DB_NAME=pos\nDB_USER=u\nDB_HOST=localhost\nDB_PORT=5432\n',
                encoding='utf-8',
            )

            with mock.patch.object(preflight.subprocess, 'run', side_effect=FileNotFoundError):
                salida = io.StringIO()
                with redirect_stderr(salida):
                    codigo = preflight.main(['backup', str(env_path), str(Path(tmp) / 'x.dump')])

        self.assertEqual(codigo, 1)
        self.assertIn('pg_dump', salida.getvalue())


class SyncConfiguradoTests(SimpleTestCase):
    def test_habilitado_con_token_real_es_0(self):
        with TemporaryDirectory() as tmp:
            ruta = Path(tmp) / 'env_cliente.env'
            ruta.write_text('SYNC_ENABLED=true\nCLOUD_API_TOKEN=abc123\n', encoding='utf-8')
            codigo = preflight.main(['sync-configurado', str(ruta)])
        self.assertEqual(codigo, 0)

    def test_token_placeholder_es_1(self):
        with TemporaryDirectory() as tmp:
            ruta = Path(tmp) / 'env_cliente.env'
            ruta.write_text(
                'SYNC_ENABLED=true\n'
                'CLOUD_API_TOKEN=PEGAR-TOKEN-DE-vincular_sucursal_token\n',
                encoding='utf-8',
            )
            codigo = preflight.main(['sync-configurado', str(ruta)])
        self.assertEqual(codigo, 1)

    def test_deshabilitado_es_1_aunque_haya_token(self):
        with TemporaryDirectory() as tmp:
            ruta = Path(tmp) / 'env_cliente.env'
            ruta.write_text('SYNC_ENABLED=false\nCLOUD_API_TOKEN=abc123\n', encoding='utf-8')
            codigo = preflight.main(['sync-configurado', str(ruta)])
        self.assertEqual(codigo, 1)

    def test_nunca_imprime_el_token(self):
        with TemporaryDirectory() as tmp:
            ruta = Path(tmp) / 'env_cliente.env'
            ruta.write_text(
                'SYNC_ENABLED=true\nCLOUD_API_TOKEN=secreto-no-debe-salir\n',
                encoding='utf-8',
            )
            salida_out, salida_err = io.StringIO(), io.StringIO()
            with redirect_stdout(salida_out), redirect_stderr(salida_err):
                preflight.main(['sync-configurado', str(ruta)])

        self.assertNotIn('secreto-no-debe-salir', salida_out.getvalue())
        self.assertNotIn('secreto-no-debe-salir', salida_err.getvalue())


class SinPythonDotenvTests(SimpleTestCase):
    def test_falla_explicito_si_falta_dotenv(self):
        with TemporaryDirectory() as tmp:
            ruta = Path(tmp) / 'env_cliente.env'
            ruta.write_text('DB_NAME=pos\n', encoding='utf-8')

            modulo_dotenv = sys.modules.pop('dotenv', None)
            with mock.patch.dict(sys.modules, {'dotenv': None}):
                with self.assertRaises(SystemExit) as ctx:
                    preflight.main(['campo', 'DB_NAME', str(ruta)])
            if modulo_dotenv is not None:
                sys.modules['dotenv'] = modulo_dotenv

        self.assertIn('python-dotenv', str(ctx.exception))
