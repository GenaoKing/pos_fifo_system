"""
apps/api/tests/test_auditoria_api.py

Reverificacion de `docs/exploracion/AUDITORIA_CODIGO_APPS_API.md`.

Los 8 hallazgos se resolvieron en junio de 2026 y siguen resueltos. Lo que este
modulo agrega es el tramo que la resolucion de entonces dejo abierto **por
decision explicita**, y que la auditoria de `apps/negocios` (NEG-001) revirtio
despues:

    "el solicitante sin negocio resoluble (SYSADMIN/global, sin ?negocio=)
     ve TODO por defecto"

Esa frase junta dos cosas distintas. Un **SYSADMIN** es un principal global y
ver todo es su trabajo — eso sigue igual y lo cubren los tests de junio. Un
**usuario huerfano** (sin negocio y sin autoridad global) NO es global: era un
error de aprovisionamiento que se leia como el permiso mas amplio del sistema.

Estos tests fijan ese contrato en la frontera de la API, que es donde la fuga
se materializaba.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.cuentas_por_cobrar.models import CuentaPorCobrar, MetodoPlazoCredito
from apps.permisos import testing as permisos_testing
from apps.sucursales.models import Sucursal
from apps.suscripciones.models import Plan, SuscripcionNegocio
from apps.ventas.models import Venta

User = get_user_model()


class ApiScopeTestCase(TestCase):
    """
    Dos negocios, una cuenta por cobrar cada uno. Todo lo que se prueba abajo
    es quien ve cuantas de esas dos.
    """

    def setUp(self):
        cache.clear()
        self.negocio_a = permisos_testing.crear_negocio('Negocio API A')
        self.negocio_b = permisos_testing.crear_negocio('Negocio API B')
        # El endpoint compone modulo (suscripcion) x permiso: sin plan, el
        # 403 vendria del entitlement y no probaria nada sobre el alcance.
        plan = Plan.objects.get(slug='empresarial')
        for negocio in (self.negocio_a, self.negocio_b):
            SuscripcionNegocio.objects.create(
                negocio=negocio, plan=plan, activa=True,
            )

        self.suc_a = Sucursal.objects.create(
            codigo='API-A', nombre='A', activa=True, negocio=self.negocio_a,
        )
        self.suc_b = Sucursal.objects.create(
            codigo='API-B', nombre='B', activa=True, negocio=self.negocio_b,
        )
        self.metodo = MetodoPlazoCredito.objects.create(
            nombre='30d auditoria api', dias_vencimiento=30, activo=True,
        )
        self.cuenta_a = self._cuenta(self.suc_a, 'API-V-A')
        self.cuenta_b = self._cuenta(self.suc_b, 'API-V-B')

    def tearDown(self):
        cache.clear()

    def _cuenta(self, sucursal, numero):
        hoy = timezone.localdate()
        cliente = Cliente.objects.create(
            tipo='PERSONAL', nombre=f'Cliente {sucursal.codigo}', activo=True,
        )
        creador = self._usuario(f'creador_{sucursal.codigo}')
        venta = Venta.objects.create(
            numero_venta=numero, fecha_venta=timezone.now(), usuario=creador,
            cliente=cliente, sucursal=sucursal, total=Decimal('100.00'),
            condicion_pago='CREDITO', estado='COMPLETADA',
        )
        return CuentaPorCobrar.objects.create(
            cliente=cliente, venta=venta, metodo_plazo=self.metodo,
            sucursal=sucursal, creado_por=creador,
            total=Decimal('100.00'), saldo=Decimal('100.00'),
            estado=CuentaPorCobrar.ESTADO_ABIERTA,
            fecha_limite=hoy + timedelta(days=5),
        )

    def _api(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def _usuario(self, username, rol='CAJERA', negocio=None):
        return User.objects.create_user(
            username=username, email=f'{username}@test.local', password='x',
            rol=rol, activo=True, negocio=negocio,
        )

    def _filas(self, respuesta):
        datos = respuesta.data
        if isinstance(datos, dict):
            return datos.get('results', datos.get('data', datos))
        return datos


class HuerfanoNoVeTodoTests(ApiScopeTestCase):
    """
    El tramo que NEG-001 revirtio de la resolucion de junio.

    La reproduccion de NEG-001: una cuenta `ADMIN` activa, no staff, no
    superusuario y con `negocio_id=NULL` recibia los datos de los dos negocios.
    `es_acceso_total` le concedia el permiso, y `negocio_actual` devolvia `None`
    —"no pude resolver"— que los consumidores leian como "sin filtro".
    """

    def test_un_admin_huerfano_no_ve_la_cartera_de_todos(self):
        huerfano = self._usuario('admin_huerfano_api', rol='ADMIN')

        respuesta = self._api(huerfano).get('/api/v1/cuentas-por-cobrar/')

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(len(self._filas(respuesta)), 0)

    def test_un_admin_huerfano_no_recupera_una_cuenta_ajena(self):
        """El IDOR: pedir el pk directo tampoco alcanza."""
        huerfano = self._usuario('admin_huerfano_api2', rol='ADMIN')

        respuesta = self._api(huerfano).get(
            f'/api/v1/cuentas-por-cobrar/{self.cuenta_b.id}/'
        )

        self.assertEqual(respuesta.status_code, 404)

    def test_un_admin_huerfano_no_consolida_reportes(self):
        huerfano = self._usuario('admin_huerfano_api3', rol='ADMIN')

        respuesta = self._api(huerfano).get('/api/v1/reportes/ventas-hoy/')

        self.assertEqual(respuesta.status_code, 403)

    def test_un_admin_huerfano_no_lista_sucursales_de_todos(self):
        huerfano = self._usuario('admin_huerfano_api4', rol='ADMIN')

        respuesta = self._api(huerfano).get('/api/v1/sucursales/status/')

        self.assertEqual(respuesta.status_code, 403)

    def test_un_usuario_con_negocio_ve_el_suyo(self):
        """El contraste: el scoping legitimo sigue funcionando."""
        propio = self._usuario('con_negocio_api', rol='ADMIN', negocio=self.negocio_a)

        respuesta = self._api(propio).get('/api/v1/cuentas-por-cobrar/')

        self.assertEqual(len(self._filas(respuesta)), 1)

    def test_un_sysadmin_sigue_viendo_todo(self):
        """
        Lo que la resolucion de junio decidio y NEG-001 NO cambio: un principal
        global ve todo, porque ver todo es su trabajo.
        """
        operador = self._usuario('sysadmin_api', rol='SYSADMIN')

        respuesta = self._api(operador).get('/api/v1/cuentas-por-cobrar/')

        self.assertEqual(len(self._filas(respuesta)), 2)

    def test_un_sysadmin_con_negocio_invalido_no_amplia(self):
        """
        NEG-002: pedir `?negocio=999999` devolvia TODOS los negocios en vez de
        un error. Un typo o un bookmark viejo ensanchaban la consulta que el
        operador intentaba acotar.
        """
        operador = self._usuario('sysadmin_api2', rol='SYSADMIN')

        respuesta = self._api(operador).get(
            '/api/v1/cuentas-por-cobrar/?negocio=999999'
        )

        self.assertEqual(len(self._filas(respuesta)), 0)


class ContratosVigentesTests(ApiScopeTestCase):
    """Los 8 hallazgos de junio siguen resueltos contra el codigo actual."""

    def test_api_003_el_handler_cae_al_usuario_de_servicio(self):
        """
        El 500 descrito no podia ocurrir —`Venta.usuario` es NOT NULL— pero el
        bug real si: el handler reventaba con IntegrityError y la venta NO se
        replicaba, dejando el evento en ERROR.
        """
        import inspect

        from apps.api.views import sync

        fuente = inspect.getsource(sync)
        self.assertIn('or sucursal.usuario_servicio', fuente)

    def test_api_004_los_viewsets_no_repiten_create_update(self):
        import inspect

        from apps.api.views import maestros

        self.assertIn('class ReadAfterWriteMixin', inspect.getsource(maestros))

    def test_api_005_el_mixin_no_muta_el_queryset_de_clase(self):
        import inspect

        from apps.api.views.maestros import SyncIncrementalMixin

        fuente = inspect.getsource(SyncIncrementalMixin)
        self.assertNotIn('self.queryset =', fuente)
        self.assertIn('get_base_queryset', fuente)

    def test_api_006_el_inventario_declara_que_es_local(self):
        from apps.api.services.reporting import build_inventario_consolidado

        datos = build_inventario_consolidado({})

        self.assertTrue(datos['es_snapshot_local'])
        self.assertEqual(datos['fuente_stock'], 'LOCAL')

    def test_api_008_el_contrato_cxc_expone_lo_financiero(self):
        from apps.api.serializers.cuentas_por_cobrar import (
            CuentaPorCobrarSerializer,
        )

        campos = set(CuentaPorCobrarSerializer.Meta.fields)
        for campo in (
            'saldo_original', 'interes_porcentaje', 'monto_interes',
            'monto_financiado',
        ):
            with self.subTest(campo=campo):
                self.assertIn(campo, campos)
