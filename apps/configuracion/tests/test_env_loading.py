"""
Tests de la carga de configuracion desde .env y su validacion (Fase 4).

Cada caso de aqui corresponde a un fallo real de instalacion:

- El `DJANGO_SECRET_KEY` de un cliente quedo truncado a 5 caracteres porque un
  `&` partia el comando en `cmd` (bug #9).
- Un nombre de impresora con espacios llegaba con las comillas pegadas al valor.
- Variables criticas ausentes daban un stack trace sin nombre en vez de un error
  claro.

El .env existe justamente para que los valores NO pasen por el interprete de
`cmd`, asi que estos tests verifican que llegan intactos.
"""
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from config.env_check import (
    MIN_SECRET_KEY,
    abortar_si_critico,
    validar_entorno,
)

BD_OK = {'default': {'NAME': 'db', 'USER': 'u', 'HOST': 'localhost'}}


class CargaDelArchivoEnvTests(SimpleTestCase):
    """Verifica el contrato de `dotenv` que usa config/settings.py."""

    def _cargar(self, contenido, entorno_previo=None):
        """Carga un .env en un dict aislado, como hace settings.py."""
        from dotenv import dotenv_values

        with TemporaryDirectory() as tmp:
            ruta = Path(tmp) / 'env_cliente.env'
            ruta.write_text(contenido, encoding='utf-8')
            valores = dict(dotenv_values(ruta, encoding='utf-8'))

        # `override=False`: lo que ya esta en el entorno gana.
        efectivo = dict(valores)
        efectivo.update(entorno_previo or {})
        return efectivo

    def test_valor_con_espacios_llega_intacto(self):
        """El nombre real de la impresora de un cliente lleva espacios."""
        env = self._cargar('THERMAL_PRINTER_NAME=2connect pos\n')

        self.assertEqual(env['THERMAL_PRINTER_NAME'], '2connect pos')

    def test_valor_con_ampersand_llega_intacto(self):
        """
        El bug #9 exacto: en `cmd` un `&` sin comillas parte el comando y el
        resto del valor se pierde. En un .env no hay interprete que lo parta.
        """
        key = 'w-5u-&hj23!kl(90)xyz&abc'
        env = self._cargar(f'DJANGO_SECRET_KEY={key}\n')

        self.assertEqual(env['DJANGO_SECRET_KEY'], key)

    def test_valor_con_parentesis_llega_intacto(self):
        env = self._cargar('DJANGO_SECRET_KEY=abc(def)ghi\n')

        self.assertEqual(env['DJANGO_SECRET_KEY'], 'abc(def)ghi')

    def test_el_entorno_real_le_gana_al_archivo(self):
        """
        `override=False` es deliberado: mantiene funcionando el rig de pruebas,
        los tests y Azure, donde la configuracion llega por entorno.
        """
        env = self._cargar('SYNC_INTERVAL=60\n', entorno_previo={'SYNC_INTERVAL': '5'})

        self.assertEqual(env['SYNC_INTERVAL'], '5')

    def test_comentarios_y_lineas_vacias_se_ignoran(self):
        env = self._cargar('# comentario\n\nDB_NAME=pos\n')

        self.assertEqual(env['DB_NAME'], 'pos')
        self.assertEqual(len(env), 1)


class ValidacionDeEntornoTests(SimpleTestCase):
    def test_secret_key_ausente_es_critico(self):
        problemas = validar_entorno({}, databases=BD_OK)

        criticos = [p for p in problemas if p.critico]
        self.assertTrue(any(p.variable == 'DJANGO_SECRET_KEY' for p in criticos))

    def test_secret_key_truncada_se_detecta(self):
        """`w-5u-` es literalmente el valor con que quedo un cliente."""
        problemas = validar_entorno({'DJANGO_SECRET_KEY': 'w-5u-'}, databases=BD_OK)

        detectado = [p for p in problemas if p.variable == 'DJANGO_SECRET_KEY']
        self.assertTrue(detectado)
        self.assertTrue(detectado[0].critico)
        self.assertIn('5 caracteres', detectado[0].mensaje)

    def test_secret_key_larga_no_alerta(self):
        problemas = validar_entorno(
            {'DJANGO_SECRET_KEY': 'x' * (MIN_SECRET_KEY + 10)}, databases=BD_OK,
        )

        self.assertEqual([p for p in problemas if p.variable == 'DJANGO_SECRET_KEY'], [])

    def test_valor_de_plantilla_sin_cambiar_es_critico(self):
        problemas = validar_entorno(
            {'DJANGO_SECRET_KEY': 'CAMBIAR-POR-KEY-UNICA-POR-INSTALACION'},
            databases=BD_OK,
        )

        self.assertTrue(any(p.critico for p in problemas))

    def test_valor_con_comillas_embebidas_alerta(self):
        problemas = validar_entorno(
            {'DJANGO_SECRET_KEY': 'x' * 40, 'THERMAL_PRINTER_NAME': '"2connect pos"'},
            databases=BD_OK,
        )

        alertas = [p for p in problemas if p.variable == 'THERMAL_PRINTER_NAME']
        self.assertTrue(alertas)
        self.assertFalse(alertas[0].critico)

    def test_base_datos_incompleta_es_critico(self):
        problemas = validar_entorno(
            {'DJANGO_SECRET_KEY': 'x' * 40},
            databases={'default': {'NAME': '', 'USER': 'u', 'HOST': 'h'}},
        )

        self.assertTrue(any(p.variable == 'DATABASES' and p.critico for p in problemas))

    def test_cloud_configurado_con_sync_apagado_alerta(self):
        """El sintoma de BUG-A visto desde la configuracion."""
        problemas = validar_entorno(
            {'DJANGO_SECRET_KEY': 'x' * 40, 'CLOUD_API_TOKEN': 'tok',
             'SYNC_ENABLED': 'false'},
            databases=BD_OK,
        )

        self.assertTrue(any(p.variable == 'SYNC_ENABLED' for p in problemas))

    def test_configuracion_sana_no_reporta_nada(self):
        problemas = validar_entorno(
            {'DJANGO_SECRET_KEY': 'x' * 40, 'CLOUD_API_URL': 'https://c',
             'CLOUD_API_TOKEN': 'tok', 'SYNC_ENABLED': 'true'},
            databases=BD_OK,
        )

        self.assertEqual(problemas, [])


class AbortarSiCriticoTests(SimpleTestCase):
    def test_devuelve_true_solo_con_criticos(self):
        salida = []
        critico = abortar_si_critico(
            validar_entorno({}, databases=BD_OK), escribir=salida.append,
        )

        self.assertTrue(critico)
        self.assertTrue(any('DJANGO_SECRET_KEY' in l for l in salida))

    def test_sin_problemas_no_aborta_ni_imprime(self):
        salida = []
        critico = abortar_si_critico([], escribir=salida.append)

        self.assertFalse(critico)
        self.assertEqual(salida, [])

    def test_solo_avisos_no_aborta(self):
        problemas = validar_entorno(
            {'DJANGO_SECRET_KEY': 'x' * 40, 'CLOUD_API_TOKEN': 'tok',
             'SYNC_ENABLED': 'false'},
            databases=BD_OK,
        )

        self.assertFalse(abortar_si_critico(problemas, escribir=lambda _: None))
