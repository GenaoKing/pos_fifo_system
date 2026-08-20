"""
Tests de `verificar_instalacion` (Fase 4).

El foco esta en la **trampa de suscripciones**: enganchar una sucursal a un
negocio sin aprovisionarle modulos apaga en silencio la impresion de tickets,
las cotizaciones, las etiquetas Zebra, las CxC y el e-CF.

Es dificil de diagnosticar porque los dos estados se ven iguales desde afuera:

    sucursal SIN negocio  -> fail-OPEN  -> todo funciona
    sucursal CON negocio  -> manda la suscripcion; sin plan quedan solo los
                             modulos core, y `impresion_termica` no es core

Este comando existe para que esa diferencia sea visible en 5 minutos y no en una
visita al cliente.
"""
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.negocios.models import Negocio
from apps.sucursales.models import Sucursal

User = get_user_model()


@override_settings(SUCURSAL_CODIGO='SD-VI')
class VerificarInstalacionTestsBase(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()  # el resolutor de modulos y get_sucursal_actual cachean

        self.user = User.objects.create_user(
            'svc_vi', 'svc_vi@test.local', 'x', rol='CAJERA',
        )
        self.sucursal = Sucursal.objects.create(
            codigo='SD-VI', nombre='Sucursal VI', activa=True,
            usuario_servicio=self.user,
        )

    def tearDown(self):
        from django.core.cache import cache
        cache.clear()

    def _reporte(self):
        import json
        out = StringIO()
        call_command('verificar_instalacion', '--json', stdout=out)
        return json.loads(out.getvalue())

    def _salida(self):
        out = StringIO()
        call_command('verificar_instalacion', stdout=out)
        return out.getvalue()


class SucursalSinNegocioTests(VerificarInstalacionTestsBase):
    def test_sin_negocio_es_modo_legacy_y_no_alerta(self):
        """
        Es un estado VALIDO: los modulos se resuelven por los flags de
        ConfiguracionNegocio (fail-open). Una instalacion nueva queda asi.
        """
        modulos = self._reporte()['modulos']

        self.assertEqual(modulos['modo'], 'legacy')
        self.assertEqual(modulos['apagados'], [])

    def test_salida_legible_lo_dice(self):
        self.assertIn('sin negocio asignado', self._salida())


class TrampaDeSuscripcionesTests(VerificarInstalacionTestsBase):
    def setUp(self):
        super().setUp()
        self.negocio = Negocio.objects.create(nombre='Negocio VI')
        self.sucursal.negocio = self.negocio
        self.sucursal.save(update_fields=['negocio'])

    def test_negocio_sin_suscripcion_apaga_los_vendibles(self):
        """El caso exacto: bootstrap_negocio sin bootstrap_suscripciones."""
        modulos = self._reporte()['modulos']

        self.assertEqual(modulos['modo'], 'suscripciones')
        self.assertFalse(modulos['aprovisionado'])
        self.assertIn('impresion_termica', modulos['apagados'],
                      'La impresion termica deberia figurar como apagada')

    def test_avisa_explicitamente_que_no_imprime(self):
        """
        Que un modulo este "apagado" no le dice nada a quien instala. Que el POS
        no imprime tickets, si.
        """
        salida = self._salida()

        self.assertIn('NO IMPRIME TICKETS', salida)
        self.assertIn('bootstrap_suscripciones', salida)

    def test_impresion_termica_efectivamente_desactivada(self):
        """
        Confirma que el reporte no miente: el gate real que usa el POS
        (`utils/impresoras/manager._is_printing_enabled`) devuelve False.
        """
        from apps.configuracion.utils import modulo_activo

        self.assertFalse(modulo_activo('impresion_termica'))

    def test_con_modulo_aprovisionado_deja_de_alertar(self):
        from apps.suscripciones.models import Modulo, NegocioModulo

        modulo, _ = Modulo.objects.get_or_create(
            key='impresion_termica', defaults={'nombre': 'Impresion termica'},
        )
        NegocioModulo.objects.create(
            negocio=self.negocio, modulo=modulo, incluido=True,
        )
        from django.core.cache import cache
        cache.clear()

        modulos = self._reporte()['modulos']

        self.assertTrue(modulos['aprovisionado'])
        self.assertNotIn('impresion_termica', modulos['apagados'])


class DiagnosticoGeneralTests(VerificarInstalacionTestsBase):
    def test_reporta_la_base_de_datos_y_los_seeds(self):
        reporte = self._reporte()

        self.assertTrue(reporte['base_datos']['conecta'])
        self.assertEqual(reporte['base_datos']['migraciones_pendientes'], [])
        self.assertEqual(reporte['seeds']['sucursal_codigo_configurado'], 'SD-VI')
        self.assertIsNotNone(reporte['seeds']['sucursal_resuelta'])

    def test_la_salida_legible_no_revienta(self):
        salida = self._salida()

        self.assertIn('VERIFICACION DE INSTALACION', salida)
        self.assertIn('MODULOS VENDIBLES', salida)
