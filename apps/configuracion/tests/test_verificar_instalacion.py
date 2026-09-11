"""
Tests de `verificar_instalacion` (Fase 4).

Cubre la **extrampa de suscripciones** (BUG-D): enganchar una sucursal a un
negocio sin aprovisionarle modulos SOLIA apagar en silencio la impresion de
tickets, las cotizaciones, las etiquetas Zebra, las CxC y el e-CF, porque:

    sucursal SIN negocio  -> fail-OPEN  -> todo funciona
    sucursal CON negocio  -> manda la suscripcion; sin plan quedaban solo los
                             modulos core, y `impresion_termica` no es core

`apps.suscripciones.engine` corrigio esa asimetria: un negocio sin
aprovisionar ahora tambien falla abierto. Este comando ya no puede reportar
"roto" para ese caso -- pero sigue senalando que el negocio no tiene
entitlements de verdad configurados, porque el fail-open es una red de
seguridad, no el estado deseado de una instalacion terminada.
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
    def test_sin_negocio_es_modo_legacy_y_enumera_los_flags(self):
        """
        CFG-013: el modo legacy consulta los flags REALES (antes devolvia
        siempre `apagados=[]` sin mirarlos). Un default-off como etiquetas_zebra
        aparece; impresion_termica (default on) no, y nada se marca roto.

        CFG-012: el reporte ya no puede materializar la config leyendola (el
        propio comando dejaria de detectar una instalacion sin
        `crear_config_inicial`) -- la crea explicitamente, como haria la
        instalacion real.
        """
        from apps.configuracion.models import ConfiguracionNegocio

        ConfiguracionNegocio.objects.create(sucursal=self.sucursal, nombre_negocio='X')

        modulos = self._reporte()['modulos']

        self.assertEqual(modulos['modo'], 'legacy')
        self.assertFalse(modulos['roto'])
        self.assertNotIn('impresion_termica', modulos['apagados'])
        self.assertIn('etiquetas_zebra', modulos['apagados'])

    def test_sin_configuracion_creada_se_reporta_roto_no_sano_por_omision(self):
        """
        CFG-012 — la reproduccion exacta del riesgo: antes, ejecutar este
        diagnostico ANTES de `crear_config_inicial` materializaba una config
        "Mi Negocio" en silencio (via el `get_or_create` de `load()`) y el
        reporte podia salir limpio. Ahora la ausencia de configuracion es un
        hallazgo explicito del propio comando, no un efecto secundario de
        haberlo corrido.
        """
        from apps.configuracion.models import ConfiguracionNegocio

        self.assertEqual(ConfiguracionNegocio.objects.count(), 0)

        modulos = self._reporte()['modulos']

        self.assertEqual(modulos['modo'], 'legacy')
        self.assertEqual(modulos['error'], 'CONFIGURACION_NO_INICIALIZADA')
        self.assertTrue(modulos['roto'])
        # El diagnostico no crea la fila que esta reportando como ausente.
        self.assertEqual(ConfiguracionNegocio.objects.count(), 0)

    def test_sin_configuracion_creada_la_salida_dice_como_arreglarlo(self):
        salida = self._salida()

        self.assertIn('No existe ninguna ConfiguracionNegocio todavia', salida)
        self.assertIn('crear_config_inicial', salida)
        self.assertIn('RESULTADO: la instalacion tiene problemas', salida)

    def test_impresion_termica_apagada_en_legacy_se_reporta_roto(self):
        """
        La reproduccion de CFG-013: con `modulo_impresion_termica=False` el gate
        real devuelve False, pero el reporte legacy afirmaba `apagados=[]`.
        """
        from django.core.cache import cache

        from apps.configuracion.models import ConfiguracionNegocio

        ConfiguracionNegocio.objects.create(
            sucursal=self.sucursal, nombre_negocio='X',
            modulo_impresion_termica=False,
        )
        cache.clear()

        modulos = self._reporte()['modulos']

        self.assertEqual(modulos['modo'], 'legacy')
        self.assertIn('impresion_termica', modulos['apagados'])
        self.assertTrue(modulos['roto'])

    def test_salida_legible_lo_dice(self):
        self.assertIn('sin negocio asignado', self._salida())


class NegocioSinAprovisionarTests(VerificarInstalacionTestsBase):
    """
    `bootstrap_negocio` sin `bootstrap_suscripciones`: el caso exacto que
    causaba BUG-D. Ya NO apaga nada -- fail-open -- pero el diagnostico sigue
    marcando que falta aprovisionar de verdad.
    """

    def setUp(self):
        super().setUp()
        self.negocio = Negocio.objects.create(nombre='Negocio VI')
        self.sucursal.negocio = self.negocio
        self.sucursal.save(update_fields=['negocio'])

    def test_negocio_sin_suscripcion_no_apaga_nada(self):
        modulos = self._reporte()['modulos']

        self.assertEqual(modulos['modo'], 'suscripciones')
        self.assertFalse(modulos['aprovisionado'])
        self.assertEqual(modulos['apagados'], [])
        self.assertFalse(modulos['roto'])

    def test_impresion_termica_efectivamente_activa(self):
        """
        Confirma que el reporte no miente: el gate real que usa el POS
        (`utils/impresoras/manager._is_printing_enabled`) devuelve True.
        """
        from apps.configuracion.utils import modulo_activo

        self.assertTrue(modulo_activo('impresion_termica'))

    def test_salida_avisa_que_falta_aprovisionar_sin_marcarlo_como_error(self):
        salida = self._salida()

        self.assertIn('AVISO', salida)
        self.assertIn('bootstrap_suscripciones', salida)
        # La seccion de modulos en si misma no debe marcar nada como roto
        # (el RESULTADO global puede seguir en rojo por otros chequeos no
        # relacionados, como el SECRET_KEY del entorno de test).
        self.assertIn('OK: todos los modulos vendibles estan activos.', salida)
        self.assertNotIn('APAGADOS', salida)

    def test_con_modulo_aprovisionado_queda_aprovisionado(self):
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

    def test_una_exclusion_explicita_sin_plan_si_apaga_y_se_reporta_roto(self):
        """
        La trampa era "nadie configuro nada". Una exclusion explicita SI es
        una decision, y si apaga impresion_termica, el comando debe seguir
        marcandolo como roto de verdad -- no es el mismo caso que el fail-open.
        """
        from apps.suscripciones.models import Modulo, NegocioModulo

        modulo, _ = Modulo.objects.get_or_create(
            key='impresion_termica', defaults={'nombre': 'Impresion termica'},
        )
        NegocioModulo.objects.create(
            negocio=self.negocio, modulo=modulo, incluido=False,
        )
        from django.core.cache import cache
        cache.clear()

        modulos = self._reporte()['modulos']

        self.assertIn('impresion_termica', modulos['apagados'])
        self.assertTrue(modulos['roto'])


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


class SeedsReportanLaSucursalCorrectaTests(VerificarInstalacionTestsBase):
    """CFG-014: el diagnostico muestra la config de la sucursal actual, no `.first()`."""

    def test_reporta_la_config_de_la_sucursal_actual_no_la_primera(self):
        from apps.configuracion.models import ConfiguracionNegocio

        # Una config de OTRA sucursal, creada ANTES (pk menor): con `.first()`
        # el diagnostico mostraba esta, no la de la sucursal resuelta.
        otra = Sucursal.objects.create(codigo='SD-OTRA', nombre='Otra', activa=True)
        ConfiguracionNegocio.objects.create(sucursal=otra, nombre_negocio='Negocio B')
        ConfiguracionNegocio.objects.create(
            sucursal=self.sucursal, nombre_negocio='Negocio A',
        )

        seeds = self._reporte()['seeds']

        self.assertEqual(seeds['configuracion_negocio'], 'Negocio A')
        self.assertIn('SD-VI', seeds['configuracion_sucursal'])


class StrictExitTests(VerificarInstalacionTestsBase):
    """CFG-014: --strict termina distinto de cero ante un estado roto."""

    def test_strict_falla_si_hay_problema(self):
        from django.core.cache import cache
        from django.core.management.base import CommandError

        from apps.configuracion.models import ConfiguracionNegocio

        ConfiguracionNegocio.objects.create(
            sucursal=self.sucursal, nombre_negocio='X',
            modulo_impresion_termica=False,  # roto: el POS no imprimiria
        )
        cache.clear()

        with self.assertRaises(CommandError):
            call_command('verificar_instalacion', '--strict')
