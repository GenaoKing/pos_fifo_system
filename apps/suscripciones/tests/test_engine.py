"""Tests del resolutor de modulos por negocio/sucursal y puede_desactivarse."""
from django.test import TestCase
from django.utils.text import slugify

from apps.negocios.models import Negocio
from apps.sucursales.models import Sucursal
from apps.suscripciones import engine, registry, seed
from apps.suscripciones.models import (
    Modulo,
    NegocioModulo,
    Plan,
    SucursalModuloOverride,
    SuscripcionNegocio,
)


def _negocio(nombre='Royal Plast'):
    return Negocio.objects.create(nombre=nombre, slug=slugify(nombre))


class ResolverTests(TestCase):
    def setUp(self):
        # Modulos/planes ya vienen de la migracion; idempotente por si acaso.
        seed.sembrar_modulos(Modulo)
        seed.crear_planes_default(Plan, Modulo)
        self.negocio = _negocio()

    def _suscribir(self, slug):
        plan = Plan.objects.get(slug=slug)
        return SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=plan, activa=True
        )

    def test_negocio_sin_aprovisionar_falla_abierto(self):
        """
        BUG-D / la trampa documentada en docs/ARQUITECTURA_MODULOS.md: un
        negocio recien creado, antes de `bootstrap_suscripciones`, no tiene ni
        suscripcion ni una sola fila de NegocioModulo. Antes esto resolvia
        SOLO core y dejaba el POS sin imprimir en silencio -- la asimetria
        con el fail-open de `negocio=None` era exactamente la trampa. Ahora
        un negocio sin aprovisionar se trata igual que un negocio sin
        resolver: todos los modulos activos.
        """
        activos = engine.modulos_negocio(self.negocio)
        self.assertEqual(activos, set(registry.keys()))
        self.assertIn('ecf', activos)
        self.assertIn('cuentas_por_cobrar', activos)

    def test_negocio_con_override_pero_sin_suscripcion_respeta_el_override(self):
        """
        La trampa solo aplica a "nadie configuro nada". En cuanto existe UNA
        fila de NegocioModulo -- aunque sea una sola exclusion, sin
        suscripcion -- ya hay una decision explicita y hay que respetarla:
        no es fail-open indiscriminado.
        """
        ecf = Modulo.objects.get(key='ecf')
        NegocioModulo.objects.create(negocio=self.negocio, modulo=ecf, incluido=False)

        activos = engine.modulos_negocio(self.negocio)
        self.assertNotIn('ecf', activos, 'la exclusion explicita se respeta')
        self.assertNotIn(
            'cuentas_por_cobrar', activos,
            'sin plan y sin incluirlo a mano, el resto sigue sin aparecer',
        )

    def test_plan_define_modulos_con_cierre(self):
        self._suscribir('empresarial')
        activos = engine.modulos_negocio(self.negocio)
        self.assertIn('ecf', activos)
        self.assertIn('cuentas_por_cobrar', activos)
        self.assertIn('ventas', activos)  # core / dependencia

    def test_override_a_la_carta_suma_y_resta(self):
        self._suscribir('basico')  # trae impresion_termica, barcode_scanner
        ecf = Modulo.objects.get(key='ecf')
        NegocioModulo.objects.create(negocio=self.negocio, modulo=ecf, incluido=True)
        barcode = Modulo.objects.get(key='barcode_scanner')
        NegocioModulo.objects.create(negocio=self.negocio, modulo=barcode, incluido=False)

        activos = engine.modulos_negocio(self.negocio)
        self.assertIn('ecf', activos)            # agregado a la carta
        self.assertNotIn('barcode_scanner', activos)  # quitado del plan

    def test_sucursal_override_apaga_local_pero_no_core(self):
        self._suscribir('pro')  # incluye cotizaciones
        suc = Sucursal.objects.create(
            codigo='RP-001', nombre='Tienda 1', activa=True, negocio=self.negocio
        )
        cotiz = Modulo.objects.get(key='cotizaciones')
        SucursalModuloOverride.objects.create(sucursal=suc, modulo=cotiz, activo=False)
        ventas = Modulo.objects.get(key='ventas')
        SucursalModuloOverride.objects.create(sucursal=suc, modulo=ventas, activo=False)

        self.assertIn('cotizaciones', engine.modulos_negocio(self.negocio))
        activos_suc = engine.modulos_activos(self.negocio, sucursal=suc)
        self.assertNotIn('cotizaciones', activos_suc)   # apagado local
        self.assertIn('ventas', activos_suc)            # core no se apaga

    def test_modulo_activo_fail_open_sin_negocio(self):
        self.assertTrue(engine.modulo_activo('ecf', negocio=None))

    def test_puede_desactivarse_bloquea_por_dependientes(self):
        self._suscribir('empresarial')
        ok, motivo = engine.puede_desactivarse(self.negocio, 'ventas')
        self.assertFalse(ok)
        self.assertIn('cuentas_por_cobrar', motivo)

    def test_puede_desactivarse_ok_sin_dependientes(self):
        self._suscribir('empresarial')
        ok, _ = engine.puede_desactivarse(self.negocio, 'etiquetas_zebra')
        self.assertTrue(ok)

    def test_invalidacion_cache_al_cambiar_override(self):
        # Provisionado (plan 'basico', sin ecf) para no caer en el fail-open
        # de negocio sin aprovisionar: se quiere probar la invalidacion de
        # cache, no la trampa de BUG-D.
        self._suscribir('basico')
        self.assertNotIn('ecf', engine.modulos_negocio(self.negocio))  # cachea
        ecf = Modulo.objects.get(key='ecf')
        NegocioModulo.objects.create(negocio=self.negocio, modulo=ecf, incluido=True)
        self.assertIn('ecf', engine.modulos_negocio(self.negocio))  # signal invalido


class BackCompatTests(TestCase):
    """La derivacion desde ConfiguracionNegocio preserva los modulos actuales."""

    def setUp(self):
        seed.sembrar_modulos(Modulo)
        seed.crear_planes_default(Plan, Modulo)
        self.negocio = _negocio('SK Performance')
        self.sucursal = Sucursal.objects.create(
            codigo='SK-001', nombre='SK', activa=True, negocio=self.negocio
        )

    def test_deriva_modulos_de_flags(self):
        from apps.configuracion.models import ConfiguracionNegocio
        ConfiguracionNegocio.objects.create(
            sucursal=self.sucursal, nombre_negocio='SK',
            modulo_ecf=True, modulo_cotizaciones=False,
        )
        activos = seed.derivar_modulos_de_flags(self.negocio, ConfiguracionNegocio)
        self.assertIn('cuentas_por_cobrar', activos)  # siempre on historicamente
        self.assertIn('ecf', activos)                 # flag on
        self.assertNotIn('cotizaciones', activos)     # flag off
