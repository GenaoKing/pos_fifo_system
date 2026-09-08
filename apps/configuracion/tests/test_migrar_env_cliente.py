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


class UnPorcentajeLiteralNoEsUnaExpansionDeCmdTests(SimpleTestCase):
    """
    Regresion de produccion (Royal Plast, 2026-08-22): la version vieja
    descartaba CUALQUIER valor con un `%`, sin distinguir un `%` literal
    (valido dentro de un `.env`) de una expansion de cmd sin resolver
    (`%NOMBRE%`). Se perdio un DJANGO_SECRET_KEY real -- su alfabeto
    aleatorio traia dos `%` -- y la instalacion habria arrancado con el
    default inseguro del repo, sin ningun error.
    """

    def _convertir(self, contenido_bat, *args):
        with TemporaryDirectory() as tmp:
            origen = Path(tmp) / 'env_cliente.bat'
            destino = Path(tmp) / 'env_cliente.env'
            origen.write_text(contenido_bat, encoding='utf-8')

            salida = StringIO()
            call_command(
                'migrar_env_cliente',
                '--origen', str(origen), '--destino', str(destino),
                *args, stdout=salida,
            )
            texto = destino.read_text(encoding='utf-8') if destino.exists() else ''
            return texto, salida.getvalue()

    @staticmethod
    def _valores(texto_env):
        return dict(
            linea.split('=', 1)
            for linea in texto_env.splitlines()
            if linea and not linea.startswith('#')
        )

    def test_un_porcentaje_suelto_en_el_secret_key_se_preserva(self):
        """
        El caso real: dos `%` en un alfabeto aleatorio, pero SIN formar un par
        `%identificador%` valido (el primero va seguido de un digito, no de
        una letra). No es una expansion de cmd -- es ruido de un generador de
        claves aleatorias.
        """
        texto, _ = self._convertir(
            'set "DJANGO_SECRET_KEY=gK9%2xLmZ8%qR4tY7wJ1nB5vC3dF6hA0sE"\n'
        )
        env = self._valores(texto)
        self.assertEqual(env['DJANGO_SECRET_KEY'], 'gK9%2xLmZ8%qR4tY7wJ1nB5vC3dF6hA0sE')

    def test_un_solo_porcentaje_sin_pareja_se_preserva(self):
        texto, _ = self._convertir('set "NOTA=Total: 100%"\n')
        env = self._valores(texto)
        self.assertEqual(env['NOTA'], 'Total: 100%')

    def test_una_expansion_de_cmd_real_sigue_omitida(self):
        """`%POS_DIR%` es exactamente lo que este chequeo SI debe descartar."""
        texto, _ = self._convertir(
            'set DB_NAME=pos\n'
            'set LOG_PATH=%POS_DIR%\\logs\n'
        )
        env = self._valores(texto)
        self.assertNotIn('LOG_PATH', env)
        self.assertIn('DB_NAME', env)

    def test_expansion_real_en_variable_no_critica_no_revienta_el_comando(self):
        """Una omision de una variable cualquiera sigue siendo solo un aviso."""
        texto, salida = self._convertir(
            'set DB_NAME=pos\n'
            'set LOG_PATH=%POS_DIR%\\logs\n'
        )
        self.assertIn('DB_NAME', self._valores(texto))
        self.assertIn('LOG_PATH', salida)

    def test_expansion_real_en_secret_key_revienta_el_comando_y_lo_dice(self):
        """
        Si DJANGO_SECRET_KEY de verdad trae una expansion sin resolver, no
        basta con omitirla en silencio: el comando tiene que fallar y decir
        exactamente cual variable y con que valor original.
        """
        with self.assertRaises(CommandError) as ctx:
            self._convertir(
                'set DB_NAME=pos\n'
                'set "DJANGO_SECRET_KEY=%ALGO_SIN_RESOLVER%"\n'
            )
        self.assertIn('DJANGO_SECRET_KEY', str(ctx.exception))

    def test_el_archivo_se_escribe_igual_aunque_falte_una_critica(self):
        """
        No perder lo demas: el resto de las variables SI quedan escritas, solo
        la critica omitida bloquea con exit distinto de cero.
        """
        with TemporaryDirectory() as tmp:
            origen = Path(tmp) / 'env_cliente.bat'
            destino = Path(tmp) / 'env_cliente.env'
            origen.write_text(
                'set DB_NAME=pos\n'
                'set "DJANGO_SECRET_KEY=%SIN_RESOLVER%"\n',
                encoding='utf-8',
            )

            with self.assertRaises(CommandError):
                call_command(
                    'migrar_env_cliente', '--origen', str(origen),
                    '--destino', str(destino), stdout=StringIO(),
                )

            self.assertTrue(destino.exists())
            env = self._valores(destino.read_text(encoding='utf-8'))
            self.assertEqual(env['DB_NAME'], 'pos')
            self.assertNotIn('DJANGO_SECRET_KEY', env)

    def test_db_password_critica_tambien_revienta(self):
        with self.assertRaises(CommandError) as ctx:
            self._convertir(
                'set DB_NAME=pos\n'
                'set "DB_PASSWORD=%SIN_RESOLVER%"\n'
            )
        self.assertIn('DB_PASSWORD', str(ctx.exception))
