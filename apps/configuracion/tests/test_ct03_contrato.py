"""
apps/configuracion/tests/test_ct03_contrato.py

Ata el contrato CT-03 (dos resolutores independientes: configuracion
efectiva y capacidades efectivas) al codigo real, con el MISMO escenario que
`docs/handoffs/cierre_prod/fixtures/C03-ct03_capacidades_efectivas_v1.json`:
un negocio con plan Pro, la sucursal '01' con su propia config y un override
negativo de 'cotizaciones', y la sucursal '02' con negocio asignado pero
SIN ConfiguracionNegocio todavia (CFG-012).

El punto que este archivo prueba explicitamente: los dos resolutores NO se
necesitan entre si. El de capacidades (`apps.suscripciones.engine`) no lee
`ConfiguracionNegocio`, asi que sigue funcionando completo aunque el de
configuracion (`apps.configuracion.utils`) todavia no tenga fila para esa
sucursal.
"""
from django.core.cache import cache
from django.test import RequestFactory, TestCase

from apps.configuracion.context_processors import config_negocio
from apps.configuracion.models import ConfiguracionNegocio, ConfiguracionNoInicializada
from apps.configuracion.utils import ConfiguracionNoResuelta, config_o_none, get_config
from apps.permisos import testing as permisos_testing
from apps.sucursales.models import Sucursal
from apps.suscripciones.engine import modulos_activos
from apps.suscripciones.models import (
    Modulo, Plan, SuscripcionNegocio, SucursalModuloOverride,
)

PLAN_PRO_MODULOS = {
    'impresion_termica', 'barcode_scanner', 'cuentas_por_cobrar',
    'cotizaciones', 'reportes_ondemand', 'dashboard', 'etiquetas_zebra',
}
CORE = {'productos', 'inventario', 'clientes', 'ventas', 'caja'}


class CT03ContratoTests(TestCase):
    """Mismo escenario que el fixture C03-ct03_capacidades_efectivas_v1.json."""

    def setUp(self):
        cache.clear()
        self.negocio = permisos_testing.crear_negocio('Ferreteria Demo')
        self.suc_01 = Sucursal.objects.create(
            codigo='CT03-01', nombre='Centro', activa=True, negocio=self.negocio,
        )
        self.suc_02 = Sucursal.objects.create(
            codigo='CT03-02', nombre='Sin config todavia', activa=True,
            negocio=self.negocio,
        )
        SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=Plan.objects.get(slug='pro'), activa=True,
        )
        self.config_01 = ConfiguracionNegocio.objects.create(
            sucursal=self.suc_01, nombre_negocio='Ferreteria Demo - Centro',
        )
        SucursalModuloOverride.objects.create(
            sucursal=self.suc_01,
            modulo=Modulo.objects.get(key='cotizaciones'),
            activo=False,
        )
        # suc_02 NO tiene ConfiguracionNegocio -- el caso CFG-012.

    def tearDown(self):
        cache.clear()

    def test_sucursal_01_config_resuelve_y_capacidades_reflejan_el_override(self):
        with self.settings(SUCURSAL_CODIGO='CT03-01'):
            cache.clear()
            config = get_config()

        self.assertEqual(config.pk, self.config_01.pk)

        activos = set(modulos_activos(self.negocio, self.suc_01))
        self.assertEqual(activos, CORE | (PLAN_PRO_MODULOS - {'cotizaciones'}))
        self.assertNotIn('cotizaciones', activos)

    def test_sucursal_02_sin_config_falla_pero_capacidades_no_dependen_de_eso(self):
        """
        El punto central del contrato: el resolutor B (capacidades) no
        necesita `ConfiguracionNegocio`. CFG-012 en el resolutor A no bloquea
        a B.
        """
        with self.settings(SUCURSAL_CODIGO='CT03-02'):
            cache.clear()
            with self.assertRaises(ConfiguracionNoInicializada) as ctx:
                get_config()

        self.assertIn('CT03-02', str(ctx.exception))
        self.assertIn('crear_config_inicial', str(ctx.exception))

        # Set COMPLETO: sin override de sucursal '02', nada se apaga.
        activos = set(modulos_activos(self.negocio, self.suc_02))
        self.assertEqual(activos, CORE | PLAN_PRO_MODULOS)

    def test_sucursal_02_render_seguro_pese_a_no_tener_config(self):
        """El context processor nunca ve CFG-012 como un 500."""
        with self.settings(SUCURSAL_CODIGO='CT03-02'):
            cache.clear()
            self.assertIsNone(config_o_none())

            ctx = config_negocio(RequestFactory().get('/'))
            self.assertIsNone(ctx['config'])
            # modulos_efectivos SI resuelve completo via el engine (negocio
            # presente): el render-safety de A no le resta capacidades a B.
            self.assertEqual(ctx['modulos_efectivos'], CORE | PLAN_PRO_MODULOS)

    def test_codigo_no_resuelto_con_varias_configs_levanta_no_resuelta(self):
        with self.settings(SUCURSAL_CODIGO='CT03-NO-EXISTE'):
            cache.clear()
            with self.assertRaises(ConfiguracionNoResuelta):
                get_config()

    def test_key_desconocida_deniega_en_vez_de_levantar(self):
        from apps.suscripciones.engine import modulo_activo

        self.assertFalse(modulo_activo('typo-de-modulo', negocio=self.negocio))
