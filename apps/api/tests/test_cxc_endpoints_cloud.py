"""
Tests de los endpoints cloud nuevos de CxC:
aging por buckets (por cuota), cartera por cliente, cobros por
sucursal/cajero y proximos vencimientos.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.cuentas_por_cobrar.models import (
    CuentaPorCobrar,
    CuotaCxC,
    MetodoPlazoCredito,
    PagoCxC,
)
from apps.sucursales.models import Sucursal
from apps.ventas.models import Venta


class CxCEndpointsCloudTests(TestCase):
    aging_url = '/api/v1/cuentas-por-cobrar/aging/'
    cartera_url = '/api/v1/cuentas-por-cobrar/cartera_clientes/'
    cobros_url = '/api/v1/cuentas-por-cobrar/cobros/'
    vencimientos_url = '/api/v1/cuentas-por-cobrar/proximos_vencimientos/'

    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='admin_cxc_cloud', email='admin_cxc_cloud@test.local',
            password='pass', rol='ADMIN', activo=True,
        )
        self.cajero2 = User.objects.create_user(
            username='cajero2_cxc_cloud', email='cajero2_cxc_cloud@test.local',
            password='pass', rol='CAJERA', activo=True,
        )
        self.sucursal = Sucursal.objects.create(codigo='SD-001', nombre='Sucursal SD', activa=True)
        self.cliente_a = Cliente.objects.create(
            tipo='CORPORATIVO', nombre='Cliente A', cedula_rnc='130000001',
            limite_credito=Decimal('50000.00'), activo=True,
        )
        self.cliente_b = Cliente.objects.create(
            tipo='PERSONAL', nombre='Cliente B', cedula_rnc='40200000002',
            limite_credito=Decimal('10000.00'), activo=True,
        )
        self.metodo = MetodoPlazoCredito.objects.create(
            nombre='30 dias cloud', dias_vencimiento=30, activo=True,
        )

        hoy = timezone.localdate()
        # Cuenta A: cuota 1 vencida hace 45 dias (bucket 31-60), cuota 2 al dia.
        self.cuenta_a = self._crear_cuenta('V-1001', self.cliente_a, '1000.00', '700.00')
        CuotaCxC.objects.create(
            cuenta=self.cuenta_a, numero=1, monto=Decimal('500.00'),
            saldo=Decimal('300.00'), fecha_vencimiento=hoy - timedelta(days=45),
            estado=CuotaCxC.ESTADO_PARCIAL,
        )
        CuotaCxC.objects.create(
            cuenta=self.cuenta_a, numero=2, monto=Decimal('500.00'),
            saldo=Decimal('400.00'), fecha_vencimiento=hoy + timedelta(days=5),
        )
        # Cuenta B: cuota vencida hace 10 dias (bucket 0-30).
        self.cuenta_b = self._crear_cuenta('V-1002', self.cliente_b, '500.00', '200.00')
        CuotaCxC.objects.create(
            cuenta=self.cuenta_b, numero=1, monto=Decimal('500.00'),
            saldo=Decimal('200.00'), fecha_vencimiento=hoy - timedelta(days=10),
            estado=CuotaCxC.ESTADO_VENCIDA,
        )

        # Pagos: admin cobro 100 (aplicado), cajero2 cobro 50 (aplicado) y
        # uno ANULADO de 999 que no debe contar.
        PagoCxC.objects.create(
            cuenta=self.cuenta_a, metodo='EFECTIVO', monto=Decimal('100.00'),
            registrado_por=self.admin,
        )
        PagoCxC.objects.create(
            cuenta=self.cuenta_b, metodo='EFECTIVO', monto=Decimal('50.00'),
            registrado_por=self.cajero2,
        )
        PagoCxC.objects.create(
            cuenta=self.cuenta_a, metodo='EFECTIVO', monto=Decimal('999.00'),
            registrado_por=self.admin, estado=PagoCxC.ESTADO_ANULADO,
        )

    def _crear_cuenta(self, numero, cliente, total, saldo):
        venta = Venta.objects.create(
            numero_venta=numero, fecha_venta=timezone.now(), usuario=self.admin,
            cliente=cliente, sucursal=self.sucursal, total=Decimal(total),
            condicion_pago='CREDITO', estado='COMPLETADA',
        )
        return CuentaPorCobrar.objects.create(
            cliente=cliente, venta=venta, metodo_plazo=self.metodo,
            total=Decimal(total), saldo=Decimal(saldo),
            saldo_original=Decimal(total),
            estado=CuentaPorCobrar.ESTADO_PARCIAL,
            fecha_limite=timezone.localdate() + timedelta(days=30),
            creado_por=self.admin, sucursal=self.sucursal,
        )

    def api(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        return client

    def test_aging_clasifica_por_cuota(self):
        response = self.api().get(self.aging_url)
        self.assertEqual(response.status_code, 200)
        datos = response.data
        self.assertEqual(Decimal(str(datos['al_dia'])), Decimal('400.00'))
        self.assertEqual(Decimal(str(datos['b_0_30'])), Decimal('200.00'))
        self.assertEqual(Decimal(str(datos['b_31_60'])), Decimal('300.00'))
        self.assertEqual(Decimal(str(datos['b_61_90'])), Decimal('0'))
        self.assertEqual(Decimal(str(datos['b_90_mas'])), Decimal('0'))
        self.assertEqual(Decimal(str(datos['total'])), Decimal('900.00'))

    def test_aging_filtra_por_sucursal(self):
        response = self.api().get(f'{self.aging_url}?sucursal=NO-EXISTE')
        self.assertEqual(Decimal(str(response.data['total'])), Decimal('0'))

    def test_cartera_clientes_agrupa_y_ordena(self):
        response = self.api().get(self.cartera_url)
        self.assertEqual(response.status_code, 200)
        filas = response.data['results']
        self.assertEqual(len(filas), 2)
        self.assertEqual(filas[0]['cliente_nombre'], 'Cliente A')
        self.assertEqual(Decimal(str(filas[0]['saldo_total'])), Decimal('700.00'))
        self.assertEqual(filas[0]['cuentas'], 1)
        self.assertEqual(Decimal(str(filas[1]['saldo_total'])), Decimal('200.00'))

    def test_cobros_por_cajero_excluye_anulados(self):
        response = self.api().get(f'{self.cobros_url}?agrupar=cajero')
        self.assertEqual(response.status_code, 200)
        resultados = {fila['clave']: fila for fila in response.data['resultados']}
        self.assertEqual(Decimal(str(resultados['admin_cxc_cloud']['total'])), Decimal('100.00'))
        self.assertEqual(Decimal(str(resultados['cajero2_cxc_cloud']['total'])), Decimal('50.00'))

    def test_cobros_por_sucursal_y_rango(self):
        response = self.api().get(self.cobros_url)
        self.assertEqual(response.status_code, 200)
        resultados = response.data['resultados']
        self.assertEqual(len(resultados), 1)
        self.assertEqual(resultados[0]['clave'], 'SD-001')
        self.assertEqual(Decimal(str(resultados[0]['total'])), Decimal('150.00'))

        # Rango en el pasado: sin cobros
        response = self.api().get(f'{self.cobros_url}?desde=2020-01-01&hasta=2020-01-31')
        self.assertEqual(response.data['resultados'], [])

    def test_proximos_vencimientos_respeta_dias(self):
        response = self.api().get(f'{self.vencimientos_url}?dias=7')
        self.assertEqual(response.status_code, 200)
        resultados = response.data['resultados']
        # Solo la cuota 2 de la cuenta A vence dentro de 7 dias
        self.assertEqual(len(resultados), 1)
        self.assertEqual(resultados[0]['numero_venta'], 'V-1001')
        self.assertEqual(resultados[0]['cuota_numero'], 2)
        self.assertEqual(resultados[0]['dias_restantes'], 5)

        # Con ?dias=1 no entra ninguna
        response = self.api().get(f'{self.vencimientos_url}?dias=1')
        self.assertEqual(response.data['resultados'], [])

    def test_endpoints_requieren_autenticacion(self):
        client = APIClient()
        for url in (self.aging_url, self.cartera_url, self.cobros_url, self.vencimientos_url):
            response = client.get(url)
            self.assertIn(response.status_code, (401, 403), url)
