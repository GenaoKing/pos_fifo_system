"""
Tests de `migrar_env_cliente` (Fase 4).

Convierte el `env_cliente.bat` de una instalacion existente al `.env` nuevo.
Royal Plast y SK ya tienen su configuracion real ahi: copiarla a mano es justo
donde se cometen los errores que esta fase busca eliminar.

Regla: preservar los valores TAL CUAL, incluidos los que estan mal. Detectar lo
sospechoso es trabajo de `verificar_instalacion`, no de la conversion.
"""
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase


class MigrarEnvClienteTests(SimpleTestCase):
    def _convertir(self, contenido_bat, *args):
        with TemporaryDirectory() as tmp:
            origen = Path(tmp) / 'env_cliente.bat'
            destino = Path(tmp) / 'env_cliente.env'
            origen.write_text(contenido_bat, encoding='utf-8')

            call_command(
                'migrar_env_cliente',
                '--origen', str(origen), '--destino', str(destino),
                *args, stdout=StringIO(),
            )
            return destino.read_text(encoding='utf-8')

    @staticmethod
    def _valores(texto_env):
        return dict(
            linea.split('=', 1)
            for linea in texto_env.splitlines()
            if linea and not linea.startswith('#')
        )

    def test_convierte_variables_basicas(self):
        env = self._valores(self._convertir(
            '@echo off\n'
            'REM comentario\n'
            'set DB_NAME=pos_autorepuestos\n'
            'set DB_PORT=5432\n'
        ))

        self.assertEqual(env['DB_NAME'], 'pos_autorepuestos')
        self.assertEqual(env['DB_PORT'], '5432')

    def test_conserva_valores_con_espacios(self):
        env = self._valores(self._convertir('set PRINTER_TERMICA=2connect pos\n'))

        self.assertEqual(env['THERMAL_PRINTER_NAME'], '2connect pos')

    def test_conserva_valores_con_ampersand_y_parentesis(self):
        """Justo los caracteres que se perdian al pasar por `cmd` (bug #9)."""
        env = self._valores(self._convertir(
            'set "DJANGO_SECRET_KEY=abc&def(ghi)jkl"\n'
        ))

        self.assertEqual(env['DJANGO_SECRET_KEY'], 'abc&def(ghi)jkl')

    def test_quita_las_comillas_del_formato_bat(self):
        env = self._valores(self._convertir('set "DB_PASSWORD=Prueba123"\n'))

        self.assertEqual(env['DB_PASSWORD'], 'Prueba123')

    def test_traduce_los_alias_de_impresora(self):
        """
        El .bat usaba PRINTER_* y los traducia a THERMAL_*/ZEBRA_*. El .env tiene
        una sola fuente de verdad, asi que el alias se resuelve al convertir.
        """
        env = self._valores(self._convertir(
            'set PRINTER_TERMICA=Epson TM20\n'
            'set PRINTER_ZEBRA=Zebra GK420\n'
        ))

        self.assertEqual(env['THERMAL_PRINTER_NAME'], 'Epson TM20')
        self.assertEqual(env['ZEBRA_PRINTER_NAME'], 'Zebra GK420')
        self.assertNotIn('PRINTER_TERMICA', env)

    def test_el_nombre_explicito_le_gana_al_alias(self):
        env = self._valores(self._convertir(
            'set PRINTER_TERMICA=Vieja\n'
            'set THERMAL_PRINTER_NAME=Nueva\n'
        ))

        self.assertEqual(env['THERMAL_PRINTER_NAME'], 'Nueva')

    def test_omite_variables_propias_del_bat(self):
        env = self._valores(self._convertir(
            'set DB_NAME=pos\n'
            'set POS_DIR=%~dp0..\n'
            'set BACKUP_DIR=%POS_DIR%\\backups\n'
        ))

        self.assertNotIn('POS_DIR', env)
        self.assertNotIn('BACKUP_DIR', env)
        self.assertIn('DB_NAME', env)

    def test_ignora_las_lineas_de_mapeo_condicional(self):
        env = self._valores(self._convertir(
            'set PRINTER_TERMICA=Epson\n'
            'if defined PRINTER_TERMICA set "THERMAL_PRINTER_NAME=%PRINTER_TERMICA%"\n'
        ))

        self.assertEqual(env['THERMAL_PRINTER_NAME'], 'Epson')

    def test_el_ultimo_set_gana_como_en_cmd(self):
        env = self._valores(self._convertir(
            'set DB_NAME=primero\nset DB_NAME=segundo\n'
        ))

        self.assertEqual(env['DB_NAME'], 'segundo')

    def test_no_sobrescribe_sin_forzar(self):
        with TemporaryDirectory() as tmp:
            origen = Path(tmp) / 'env_cliente.bat'
            destino = Path(tmp) / 'env_cliente.env'
            origen.write_text('set DB_NAME=pos\n', encoding='utf-8')
            destino.write_text('YA_EXISTE=1\n', encoding='utf-8')

            with self.assertRaises(CommandError):
                call_command('migrar_env_cliente', '--origen', str(origen),
                             '--destino', str(destino), stdout=StringIO())

            self.assertIn('YA_EXISTE', destino.read_text(encoding='utf-8'))

    def test_archivo_sin_variables_falla_claro(self):
        with TemporaryDirectory() as tmp:
            origen = Path(tmp) / 'env_cliente.bat'
            origen.write_text('@echo off\nREM nada\n', encoding='utf-8')

            with self.assertRaises(CommandError):
                call_command('migrar_env_cliente', '--origen', str(origen),
                             '--destino', str(Path(tmp) / 'x.env'), stdout=StringIO())
