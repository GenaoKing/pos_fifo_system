"""
Tests del rewire de apps/configuracion/utils.py:modulo_activo al resolutor por
tenant, con fallback legacy a los flags de ConfiguracionNegocio.
"""
from django.test import TestCase, override_settings

from apps.configuracion.models import ConfiguracionNegocio
from apps.configuracion.utils import modulo_activo
from apps.negocios.models import Negocio
from apps.sucursales.models import Sucursal
from apps.suscripciones import seed
from apps.suscripciones.models import Modulo, Plan, SuscripcionNegocio


@override_settings(SUCURSAL_CODIGO='SD-001')
class ModuloActivoTenantTests(TestCase):
    """Sucursal con negocio resuelto -> usa el entitlement por tenant."""

    def setUp(self):
        seed.sembrar_modulos(Modulo)
        seed.crear_planes_default(Plan, Modulo)
        self.negocio = Negocio.objects.create(nombre='Royal Plast', slug='royal-plast')
        self.sucursal = Sucursal.objects.create(
            codigo='SD-001', nombre='SD', activa=True, negocio=self.negocio
        )

    def test_sin_suscripcion_solo_core(self):
        self.assertFalse(modulo_activo('ecf'))
        self.assertTrue(modulo_activo('ventas'))  # core

    def test_plan_empresarial_activa_vendibles(self):
        SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=Plan.objects.get(slug='empresarial'), activa=True
        )
        self.assertTrue(modulo_activo('ecf'))
        self.assertTrue(modulo_activo('cuentas_por_cobrar'))

    def test_alias_financiacion_coop(self):
        SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=Plan.objects.get(slug='empresarial'), activa=True
        )
        # El nombre legacy 'financiacion_coop' mapea a la key 'financiacion'.
        self.assertTrue(modulo_activo('financiacion_coop'))
        self.assertTrue(modulo_activo('financiacion'))


@override_settings(SUCURSAL_CODIGO='SD-LEGACY')
class ModuloActivoFallbackLegacyTests(TestCase):
    """Sucursal sin negocio (instalacion no provisionada) -> fallback al flag."""

    def setUp(self):
        seed.sembrar_modulos(Modulo)
        self.sucursal = Sucursal.objects.create(
            codigo='SD-LEGACY', nombre='Legacy', activa=True  # sin negocio
        )
        ConfiguracionNegocio.objects.create(
            sucursal=self.sucursal, nombre_negocio='Legacy',
            modulo_ecf=True, modulo_cotizaciones=False, modulo_financiacion_coop=True,
        )

    def test_fallback_respeta_flags(self):
        self.assertTrue(modulo_activo('ecf'))                # flag on
        self.assertTrue(modulo_activo('financiacion_coop'))  # alias -> modulo_financiacion_coop on
        self.assertFalse(modulo_activo('cotizaciones'))      # flag off

    def test_fallback_core_y_vendible_sin_flag(self):
        self.assertTrue(modulo_activo('ventas'))               # core
        self.assertTrue(modulo_activo('cuentas_por_cobrar'))   # vendible sin flag -> on
