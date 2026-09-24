"""
Tests de `rotar_secret_key` (C01, punto 5).

Reglas del comando:

- Opera SOLO sobre el `.env` canonico, nunca sobre `env_cliente.bat` (ya no lo
  lee la aplicacion desde la Fase 4).
- Reemplaza UNICAMENTE la linea de `DJANGO_SECRET_KEY`, preservando el resto
  del archivo (comentarios, orden, otras variables) tal cual.
- Nunca imprime el valor de la key, ni la anterior ni la nueva.
- Deja un respaldo recuperable de la version previa antes de escribir.
"""
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase


class RotarSecretKeyTests(SimpleTestCase):
    def _archivo(self, tmp, contenido):
        ruta = Path(tmp) / 'env_cliente.env'
        ruta.write_text(contenido, encoding='utf-8')
        return ruta

    def _rotar(self, ruta, *args, entrada='SI\n'):
        salida = StringIO()
        with mock.patch('builtins.input', return_value=entrada.strip()):
            call_command('rotar_secret_key', '--archivo', str(ruta), *args, stdout=salida)
        return salida.getvalue()

    def test_reemplaza_solo_la_linea_de_secret_key(self):
        with TemporaryDirectory() as tmp:
            ruta = self._archivo(
                tmp,
                '# comentario que debe sobrevivir\n'
                'DB_NAME=pos_rp\n'
                'DJANGO_SECRET_KEY=vieja-y-corta\n'
                'SUCURSAL_CODIGO=01\n',
            )
            self._rotar(ruta, '--forzar')

            contenido = ruta.read_text(encoding='utf-8')
            lineas = contenido.splitlines()
            self.assertIn('# comentario que debe sobrevivir', lineas)
            self.assertIn('DB_NAME=pos_rp', lineas)
            self.assertIn('SUCURSAL_CODIGO=01', lineas)
            self.assertNotIn('DJANGO_SECRET_KEY=vieja-y-corta', contenido)

            nueva = [l for l in lineas if l.startswith('DJANGO_SECRET_KEY=')]
            self.assertEqual(len(nueva), 1)
            self.assertGreaterEqual(len(nueva[0].split('=', 1)[1]), 20)

    def test_agrega_la_linea_si_no_existia(self):
        with TemporaryDirectory() as tmp:
            ruta = self._archivo(tmp, 'DB_NAME=pos\n')
            self._rotar(ruta, '--forzar')

            contenido = ruta.read_text(encoding='utf-8')
            self.assertIn('DB_NAME=pos', contenido)
            self.assertTrue(any(
                l.startswith('DJANGO_SECRET_KEY=') for l in contenido.splitlines()
            ))

    def test_falla_si_no_existe_el_env_canonico(self):
        with TemporaryDirectory() as tmp:
            ruta = Path(tmp) / 'env_cliente.env'
            with self.assertRaises(CommandError) as ctx:
                self._rotar(ruta, '--forzar')
            self.assertIn(str(ruta), str(ctx.exception))

    def test_sugiere_migrar_si_solo_existe_el_bat(self):
        with TemporaryDirectory() as tmp:
            ruta = Path(tmp) / 'env_cliente.env'
            (Path(tmp) / 'env_cliente.bat').write_text('set DB_NAME=pos\n', encoding='utf-8')
            with self.assertRaises(CommandError) as ctx:
                self._rotar(ruta, '--forzar')
            self.assertIn('migrar_env_cliente', str(ctx.exception))

    def test_no_toca_el_bat_aunque_exista(self):
        with TemporaryDirectory() as tmp:
            ruta = self._archivo(tmp, 'DJANGO_SECRET_KEY=vieja\n')
            bat = Path(tmp) / 'env_cliente.bat'
            bat.write_text('set DJANGO_SECRET_KEY=vieja\n', encoding='utf-8')

            self._rotar(ruta, '--forzar')

            self.assertEqual(bat.read_text(encoding='utf-8'), 'set DJANGO_SECRET_KEY=vieja\n')

    def test_deja_respaldo_recuperable(self):
        with TemporaryDirectory() as tmp:
            ruta = self._archivo(tmp, 'DJANGO_SECRET_KEY=version-anterior-larga-1234567890\n')
            self._rotar(ruta, '--forzar')

            respaldos = list(Path(tmp).glob('env_cliente.env.bak_*'))
            self.assertEqual(len(respaldos), 1)
            self.assertIn('version-anterior-larga-1234567890', respaldos[0].read_text(encoding='utf-8'))

    def test_nunca_imprime_el_valor_de_la_key(self):
        with TemporaryDirectory() as tmp:
            ruta = self._archivo(tmp, 'DJANGO_SECRET_KEY=valor-viejo-bien-largo-1234567890\n')
            salida = self._rotar(ruta, '--forzar')

            nueva_key = [
                l for l in ruta.read_text(encoding='utf-8').splitlines()
                if l.startswith('DJANGO_SECRET_KEY=')
            ][0].split('=', 1)[1]

            self.assertNotIn('valor-viejo-bien-largo-1234567890', salida)
            self.assertNotIn(nueva_key, salida)

    def test_dry_run_no_escribe_nada(self):
        with TemporaryDirectory() as tmp:
            ruta = self._archivo(tmp, 'DJANGO_SECRET_KEY=original-larga-1234567890\n')
            original = ruta.read_text(encoding='utf-8')

            self._rotar(ruta, '--dry-run')

            self.assertEqual(ruta.read_text(encoding='utf-8'), original)
            self.assertEqual(list(Path(tmp).glob('*.bak_*')), [])

    def test_sin_forzar_pide_confirmacion_y_cancela_si_no_es_si(self):
        with TemporaryDirectory() as tmp:
            ruta = self._archivo(tmp, 'DJANGO_SECRET_KEY=original-larga-1234567890\n')
            original = ruta.read_text(encoding='utf-8')

            self._rotar(ruta, entrada='no\n')

            self.assertEqual(ruta.read_text(encoding='utf-8'), original)

    def test_sin_forzar_confirma_con_si(self):
        with TemporaryDirectory() as tmp:
            ruta = self._archivo(tmp, 'DJANGO_SECRET_KEY=original-larga-1234567890\n')

            self._rotar(ruta, entrada='SI\n')

            self.assertNotIn('original-larga-1234567890', ruta.read_text(encoding='utf-8'))

    def test_ultima_ocurrencia_activa_es_la_que_se_reemplaza(self):
        with TemporaryDirectory() as tmp:
            ruta = self._archivo(
                tmp,
                '# DJANGO_SECRET_KEY=comentada-no-cuenta\n'
                'DJANGO_SECRET_KEY=primera-vieja-1234567890\n'
                'DB_NAME=pos\n'
                'DJANGO_SECRET_KEY=segunda-vieja-1234567890\n',
            )
            self._rotar(ruta, '--forzar')

            lineas = ruta.read_text(encoding='utf-8').splitlines()
            activas = [l for l in lineas if l.startswith('DJANGO_SECRET_KEY=')]
            self.assertEqual(len(activas), 1)
            self.assertIn('# DJANGO_SECRET_KEY=comentada-no-cuenta', lineas)
            self.assertIn('DB_NAME=pos', lineas)
