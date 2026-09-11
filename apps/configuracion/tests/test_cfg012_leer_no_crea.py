"""
apps/configuracion/tests/test_cfg012_leer_no_crea.py

CFG-012 — leer la configuracion no puede crearla ni tocar sus conteos o
timestamps. Antes, `ConfiguracionNegocio.load()` hacia `get_or_create()` y el
context processor lo invocaba en CADA render (login, error, admin incluidos):
abrir cualquier pagina en una instalacion sin `crear_config_inicial` corrido
creaba en silencio una fila "Mi Negocio".

Este modulo prueba, contra el codigo real (no un fixture que ya materializa
la fila): instalaciones legacy sin config, una/varias sucursales sin config
propia, lecturas repetidas sin side effects, y que el bootstrap explicito
(`crear_config_inicial`, `ConfiguracionNegocio.bootstrap()`) sigue
funcionando igual que antes.
"""
from django.core.cache import cache
from django.core.management import call_command
from django.test import RequestFactory, TestCase

from apps.configuracion.context_processors import config_negocio
from apps.configuracion.models import ConfiguracionNegocio, ConfiguracionNoInicializada
from apps.configuracion.utils import (
    ConfiguracionNoResuelta,
    config_o_none,
    get_config,
    modulos_efectivos_o_vacio,
)
from apps.sucursales.models import Sucursal
from apps.tenancy.context import TenantContextError, force_tenancy


class SinConfiguracionTests(TestCase):
    """Instalacion legacy (sin sucursal) que todavia no corrio crear_config_inicial."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_get_config_falla_en_vez_de_crear(self):
        self.assertEqual(ConfiguracionNegocio.objects.count(), 0)

        with self.assertRaises(ConfiguracionNoInicializada) as ctx:
            get_config()

        self.assertIn('crear_config_inicial', str(ctx.exception))
        self.assertEqual(
            ConfiguracionNegocio.objects.count(), 0,
            'Una lectura fallida no puede dejar una fila creada.',
        )

    def test_load_directo_falla_en_vez_de_crear(self):
        with self.assertRaises(ConfiguracionNoInicializada):
            ConfiguracionNegocio.load()

        self.assertEqual(ConfiguracionNegocio.objects.count(), 0)

    def test_lecturas_repetidas_no_crean_nada(self):
        """Render/GET repetido: ni la primera ni la decima lectura crean fila."""
        for _ in range(10):
            cache.clear()
            with self.assertRaises(ConfiguracionNoInicializada):
                get_config()

        self.assertEqual(ConfiguracionNegocio.objects.count(), 0)

    def test_config_o_none_no_propaga_ni_crea(self):
        self.assertIsNone(config_o_none())
        self.assertEqual(ConfiguracionNegocio.objects.count(), 0)

    def test_modulos_efectivos_o_vacio_no_propaga_ni_crea(self):
        self.assertEqual(modulos_efectivos_o_vacio(), set())
        self.assertEqual(ConfiguracionNegocio.objects.count(), 0)

    def test_context_processor_renderiza_seguro_sin_config(self):
        """
        La reproduccion exacta del hallazgo: el context processor corre en
        CADA pagina (login, error, admin). Sin fila, debe devolver un
        contexto seguro, no un 500 ni una fila creada de la nada.
        """
        peticion = RequestFactory().get('/login/')

        ctx = config_negocio(peticion)

        self.assertIsNone(ctx['config'])
        self.assertEqual(ctx['modulos_efectivos'], set())
        self.assertEqual(ConfiguracionNegocio.objects.count(), 0)

    def test_context_processor_repetido_sigue_sin_crear(self):
        """Varias 'páginas' vistas en secuencia (p.ej. 404 tras 404 de un scanner)."""
        peticion = RequestFactory().get('/algo-que-no-existe/')
        for _ in range(5):
            ctx = config_negocio(peticion)
            self.assertIsNone(ctx['config'])

        self.assertEqual(ConfiguracionNegocio.objects.count(), 0)


class ConMultiplesSucursalesTests(TestCase):
    """Una tiene config propia, la otra todavia no."""

    def setUp(self):
        cache.clear()
        self.suc_con_config = Sucursal.objects.create(
            codigo='CFG012-A', nombre='Tienda con config', activa=True,
        )
        self.suc_sin_config = Sucursal.objects.create(
            codigo='CFG012-B', nombre='Tienda sin config', activa=True,
        )
        self.config = ConfiguracionNegocio.objects.create(
            sucursal=self.suc_con_config, nombre_negocio='Con Config', rnc='999',
        )

    def tearDown(self):
        cache.clear()

    def test_la_sucursal_con_config_lee_bien(self):
        config = ConfiguracionNegocio.load(sucursal=self.suc_con_config)
        self.assertEqual(config.pk, self.config.pk)

    def test_la_sucursal_sin_config_falla_nombrandola_sin_crear_nada(self):
        with self.assertRaises(ConfiguracionNoInicializada) as ctx:
            ConfiguracionNegocio.load(sucursal=self.suc_sin_config)

        self.assertIn('CFG012-B', str(ctx.exception))
        self.assertIn('crear_config_inicial', str(ctx.exception))
        # No se creo nada para la sucursal sin config, y la otra sigue intacta.
        self.assertEqual(ConfiguracionNegocio.objects.count(), 1)

    def test_get_config_de_la_sucursal_sin_config_falla_via_settings(self):
        with self.settings(SUCURSAL_CODIGO='CFG012-B'):
            cache.clear()
            with self.assertRaises(ConfiguracionNoInicializada):
                get_config()

        self.assertEqual(ConfiguracionNegocio.objects.count(), 1)

    def test_repetir_la_lectura_de_la_existente_no_cambia_su_timestamp(self):
        """Lecturas repetidas sin side effects: ni conteo ni fecha_modificacion cambian."""
        fecha_original = self.config.fecha_modificacion

        for _ in range(5):
            cache.clear()
            leida = ConfiguracionNegocio.load(sucursal=self.suc_con_config)
            self.assertEqual(leida.pk, self.config.pk)

        self.config.refresh_from_db()
        self.assertEqual(self.config.fecha_modificacion, fecha_original)
        self.assertEqual(ConfiguracionNegocio.objects.count(), 1)


class BootstrapExplicitoTests(TestCase):
    """El bootstrap explicito (fuera de load()) sigue siendo idempotente."""

    def setUp(self):
        cache.clear()
        self.sucursal = Sucursal.objects.create(
            codigo='CFG012-BOOT', nombre='Bootstrap', activa=True,
        )

    def tearDown(self):
        cache.clear()

    def test_bootstrap_crea_una_sola_vez_por_sucursal(self):
        primero = ConfiguracionNegocio.bootstrap(sucursal=self.sucursal)
        segundo = ConfiguracionNegocio.bootstrap(sucursal=self.sucursal)

        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(ConfiguracionNegocio.objects.count(), 1)

    def test_bootstrap_legacy_sin_sucursal_es_idempotente(self):
        primero = ConfiguracionNegocio.bootstrap()
        segundo = ConfiguracionNegocio.bootstrap()

        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(ConfiguracionNegocio.objects.count(), 1)

    def test_una_vez_creada_por_bootstrap_load_la_lee_sin_crear_otra(self):
        creada = ConfiguracionNegocio.bootstrap(sucursal=self.sucursal)

        leida = ConfiguracionNegocio.load(sucursal=self.sucursal)

        self.assertEqual(leida.pk, creada.pk)
        self.assertEqual(ConfiguracionNegocio.objects.count(), 1)

    def test_crear_config_inicial_sigue_funcionando_igual(self):
        """
        El comando de instalacion real no pasa por `load()` -- ya crea de
        forma explicita. CFG-012 no debe alterar su conducta.
        """
        call_command(
            'crear_config_inicial',
            '--sucursal', self.sucursal.codigo,
            '--nombre', 'Negocio de Prueba',
        )

        config = ConfiguracionNegocio.objects.get(sucursal=self.sucursal)
        self.assertEqual(config.nombre_negocio, 'Negocio de Prueba')

        # Reejecutarlo es idempotente (actualiza la misma fila, no duplica).
        call_command(
            'crear_config_inicial',
            '--sucursal', self.sucursal.codigo,
            '--nombre', 'Negocio Renombrado',
        )
        self.assertEqual(ConfiguracionNegocio.objects.count(), 1)
        config.refresh_from_db()
        self.assertEqual(config.nombre_negocio, 'Negocio Renombrado')


class SinTenantActivoTests(TestCase):
    """
    BUG-E: bajo tenancy sin tenant activo en contexto, ni el context
    processor ni las variantes render-safe pueden tumbar la pagina.
    """

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_config_o_none_atrapa_tenant_context_error(self):
        with force_tenancy(True):
            self.assertIsNone(config_o_none())

    def test_modulos_efectivos_o_vacio_atrapa_tenant_context_error(self):
        with force_tenancy(True):
            self.assertEqual(modulos_efectivos_o_vacio(), set())

    def test_context_processor_seguro_sin_tenant_activo(self):
        with force_tenancy(True):
            ctx = config_negocio(RequestFactory().get('/'))

        self.assertIsNone(ctx['config'])
        self.assertEqual(ctx['modulos_efectivos'], set())

    def test_get_config_directo_si_propaga_tenant_context_error(self):
        """
        A diferencia del context processor, una decision de negocio real NO
        debe disimular la falta de tenant: get_config() sigue fallando fuerte.
        """
        with force_tenancy(True):
            with self.assertRaises(TenantContextError):
                get_config()
