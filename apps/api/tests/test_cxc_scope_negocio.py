"""
Aislamiento multi-tenant del endpoint de cartera (CxC) — API-001.

Verifica que list/retrieve y las acciones de agregacion (resumen, aging,
cartera_clientes, cobros, proximos_vencimientos) respetan el scope del tenant:
  - token de sucursal       -> solo SU sucursal (y 404 en pk ajeno),
  - usuario con negocio      -> solo SU negocio,
  - SYSADMIN/global          -> todo; ?negocio=<id> lo acota.

NOTA (2026-08-30): la resolucion de junio dejo escrito que "el solicitante sin
negocio resoluble ve TODO". NEG-001 corrigio esa regla: ver todo depende de ser
un principal global VERIFICADO, no de que la resolucion del negocio haya
fallado. Lo que estos tests fijan sigue vigente —un SYSADMIN es global— y el
caso que la regla vieja abria, el usuario huerfano, se cubre en
`test_auditoria_api.py`.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.cuentas_por_cobrar.models import (
    CuentaPorCobrar,
    CuotaCxC,
    MetodoPlazoCredito,
    PagoCxC,
)
from apps.permisos import testing
from apps.sucursales.models import Sucursal
from apps.suscripciones.models import Plan, SuscripcionNegocio
from apps.ventas.models import Venta

User = get_user_model()


class CxCScopeNegocioTests(TestCase):
    base = '/api/v1/cuentas-por-cobrar/'

    def setUp(self):
        self.neg_a = testing.crear_negocio('Negocio A')
        self.neg_b = testing.crear_negocio('Negocio B')
        # El endpoint compone modulo (suscripcion) x permiso.
        plan = Plan.objects.get(slug='empresarial')
        for neg in (self.neg_a, self.neg_b):
            SuscripcionNegocio.objects.create(negocio=neg, plan=plan, activa=True)

        # Usuarios de negocio con permiso de lectura (asignar fija user.negocio).
        self.user_a = self._cajera('cobrador_a')
        testing.asignar(
            self.user_a,
            testing.crear_rol(self.neg_a, 'Cobrador', ['cuentas_por_cobrar.ver']),
        )
        self.user_b = self._cajera('cobrador_b')
        testing.asignar(
            self.user_b,
            testing.crear_rol(self.neg_b, 'Cobrador', ['cuentas_por_cobrar.ver']),
        )

        # SYSADMIN: principal global verificado. Ve todo por su autoridad,
        # no porque `negocio_actual` devuelva None (ver NOTA del encabezado).
        self.sysadmin = User.objects.create_user(
            username='sysadmin_cxc', email='sys_cxc@test.local', password='x',
            rol='SYSADMIN', activo=True,
        )

        # Sucursales con usuario_servicio + token (path de sync).
        self.svc_a = self._cajera('svc_a')
        self.suc_a = Sucursal.objects.create(
            codigo='A-001', nombre='Suc A', activa=True,
            usuario_servicio=self.svc_a, negocio=self.neg_a,
        )
        self.token_a = Token.objects.create(user=self.svc_a)
        self.svc_b = self._cajera('svc_b')
        self.suc_b = Sucursal.objects.create(
            codigo='B-001', nombre='Suc B', activa=True,
            usuario_servicio=self.svc_b, negocio=self.neg_b,
        )
        self.token_b = Token.objects.create(user=self.svc_b)

        self.metodo = MetodoPlazoCredito.objects.create(
            nombre='30d scope', dias_vencimiento=30, activo=True,
        )
        self.cli_a = Cliente.objects.create(
            tipo='PERSONAL', nombre='Cli A', cedula_rnc='40200000001', activo=True,
        )
        self.cli_b = Cliente.objects.create(
            tipo='PERSONAL', nombre='Cli B', cedula_rnc='40200000002', activo=True,
        )

        self.cuenta_a = self._cuenta('SA-1', self.cli_a, self.suc_a, self.user_a, '1000.00')
        self.cuenta_b = self._cuenta('SB-1', self.cli_b, self.suc_b, self.user_b, '500.00')

    def _cajera(self, username):
        return User.objects.create_user(
            username=username, email=f'{username}@test.local', password='x',
            rol='CAJERA', activo=True,
        )

    def _cuenta(self, numero, cliente, sucursal, creado_por, saldo):
        hoy = timezone.localdate()
        venta = Venta.objects.create(
            numero_venta=numero, fecha_venta=timezone.now(), usuario=creado_por,
            cliente=cliente, sucursal=sucursal, total=Decimal(saldo),
            condicion_pago='CREDITO', estado='COMPLETADA',
        )
        cuenta = CuentaPorCobrar.objects.create(
            cliente=cliente, venta=venta, metodo_plazo=self.metodo,
            total=Decimal(saldo), saldo=Decimal(saldo),
            estado=CuentaPorCobrar.ESTADO_ABIERTA,
            fecha_limite=hoy + timedelta(days=5), creado_por=creado_por, sucursal=sucursal,
        )
        CuotaCxC.objects.create(
            cuenta=cuenta, numero=1, monto=Decimal(saldo), saldo=Decimal(saldo),
            fecha_vencimiento=hoy + timedelta(days=5),
        )
        PagoCxC.objects.create(
            cuenta=cuenta, metodo=PagoCxC.METODO_EFECTIVO, monto=Decimal('50.00'),
            registrado_por=creado_por,
        )
        return cuenta

    def _api(self, user=None, token=None):
        client = APIClient()
        if token:
            client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        elif user:
            client.force_authenticate(user=user)
        return client

    # --- list / retrieve ---

    def test_token_sucursal_solo_ve_su_sucursal(self):
        r = self._api(token=self.token_a).get(self.base)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['count'], 1)
        self.assertEqual(r.data['results'][0]['numero_venta'], 'SA-1')

    def test_token_sucursal_no_recupera_pk_ajeno(self):
        r = self._api(token=self.token_a).get(f'{self.base}{self.cuenta_b.id}/')
        self.assertEqual(r.status_code, 404)

    def test_usuario_negocio_solo_ve_su_negocio(self):
        r = self._api(user=self.user_a).get(self.base)
        self.assertEqual(r.data['count'], 1)
        self.assertEqual(r.data['results'][0]['numero_venta'], 'SA-1')
        cruzado = self._api(user=self.user_a).get(f'{self.base}{self.cuenta_b.id}/')
        self.assertEqual(cruzado.status_code, 404)

    def test_sysadmin_ve_todo(self):
        r = self._api(user=self.sysadmin).get(self.base)
        self.assertEqual(r.data['count'], 2)

    def test_sysadmin_con_negocio_param_acota(self):
        r = self._api(user=self.sysadmin).get(f'{self.base}?negocio={self.neg_a.id}')
        self.assertEqual(r.data['count'], 1)
        self.assertEqual(r.data['results'][0]['numero_venta'], 'SA-1')

    # --- acciones de agregacion ---

    def test_resumen_scoped(self):
        r = self._api(user=self.user_a).get(f'{self.base}resumen/')
        self.assertEqual(Decimal(str(r.data['cartera_total'])), Decimal('1000.00'))
        self.assertEqual(r.data['cuentas_abiertas'], 1)

    def test_resumen_sysadmin_global(self):
        r = self._api(user=self.sysadmin).get(f'{self.base}resumen/')
        self.assertEqual(Decimal(str(r.data['cartera_total'])), Decimal('1500.00'))
        self.assertEqual(r.data['cuentas_abiertas'], 2)

    def test_aging_scoped(self):
        r = self._api(user=self.user_a).get(f'{self.base}aging/')
        self.assertEqual(Decimal(str(r.data['total'])), Decimal('1000.00'))

    def test_cartera_clientes_scoped(self):
        r = self._api(user=self.user_a).get(f'{self.base}cartera_clientes/')
        self.assertEqual(r.data['count'], 1)
        self.assertEqual(r.data['results'][0]['cliente_nombre'], 'Cli A')

    def test_cobros_scoped(self):
        r = self._api(user=self.user_a).get(f'{self.base}cobros/')
        resultados = r.data['resultados']
        self.assertEqual(len(resultados), 1)
        self.assertEqual(Decimal(str(resultados[0]['total'])), Decimal('50.00'))

    def test_proximos_vencimientos_scoped(self):
        r = self._api(user=self.user_a).get(f'{self.base}proximos_vencimientos/?dias=30')
        self.assertEqual(len(r.data['resultados']), 1)
        self.assertEqual(r.data['resultados'][0]['numero_venta'], 'SA-1')
