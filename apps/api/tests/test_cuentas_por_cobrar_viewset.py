"""
Tests del endpoint read-only de cartera (B15 / 5.H).

Cubre: permisos (lectura sucursal+admin, escritura prohibida), forma de la
respuesta de lista y detalle, filtros (search/estado/vencidas), el calculo de
`esta_vencida` desfasado del `estado`, y los agregados de `resumen/`.
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
from apps.sucursales.models import Sucursal
from apps.ventas.models import Venta


class CuentaPorCobrarViewSetTests(TestCase):
    base_url = '/api/v1/cuentas-por-cobrar/'
    resumen_url = '/api/v1/cuentas-por-cobrar/resumen/'

    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='admin_cxc', email='admin_cxc@test.local',
            password='pass', rol='ADMIN', activo=True,
        )
        self.cajera = User.objects.create_user(
            username='cajera_cxc', email='cajera_cxc@test.local',
            password='pass', rol='CAJERA', activo=True,
        )
        self.sucursal_user = User.objects.create_user(
            username='svc_cxc', email='svc_cxc@test.local',
            password='pass', rol='CAJERA', activo=True,
        )
        self.sucursal = Sucursal.objects.create(
            codigo='SD-001', nombre='Sucursal SD', activa=True,
            usuario_servicio=self.sucursal_user,
        )
        self.sucursal_token = Token.objects.create(user=self.sucursal_user)

        self.cliente = Cliente.objects.create(
            tipo='CORPORATIVO', nombre='Royal Plast SRL',
            cedula_rnc='130123456', limite_credito=Decimal('50000.00'), activo=True,
        )
        self.otro_cliente = Cliente.objects.create(
            tipo='PERSONAL', nombre='Juan Perez',
            cedula_rnc='40212345678', limite_credito=Decimal('10000.00'), activo=True,
        )
        self.metodo = MetodoPlazoCredito.objects.create(
            nombre='30 dias', dias_vencimiento=30, activo=True,
        )

        hoy = timezone.localdate()

        # Cuenta abierta, NO vencida (vence en el futuro).
        self.cuenta_abierta = self._crear_cuenta(
            numero='V-0001', cliente=self.cliente,
            total='1000.00', saldo='1000.00',
            estado=CuentaPorCobrar.ESTADO_ABIERTA,
            fecha_limite=hoy + timedelta(days=10),
        )
        # Cuenta PARCIAL cuya fecha limite ya paso -> esta_vencida pese a estado.
        self.cuenta_vencida_de_hecho = self._crear_cuenta(
            numero='V-0002', cliente=self.cliente,
            total='2000.00', saldo='800.00',
            estado=CuentaPorCobrar.ESTADO_PARCIAL,
            fecha_limite=hoy - timedelta(days=5),
        )
        # Cuenta PAGADA (saldo 0): fuera de cartera/vencido.
        self.cuenta_pagada = self._crear_cuenta(
            numero='V-0003', cliente=self.otro_cliente,
            total='500.00', saldo='0.00',
            estado=CuentaPorCobrar.ESTADO_PAGADA,
            fecha_limite=hoy - timedelta(days=1),
        )

    def _crear_cuenta(self, *, numero, cliente, total, saldo, estado, fecha_limite):
        venta = Venta.objects.create(
            numero_venta=numero, fecha_venta=timezone.now(), usuario=self.admin,
            cliente=cliente, sucursal=self.sucursal, total=Decimal(total),
            condicion_pago='CREDITO', estado='COMPLETADA',
        )
        return CuentaPorCobrar.objects.create(
            cliente=cliente, venta=venta, metodo_plazo=self.metodo,
            total=Decimal(total), saldo=Decimal(saldo), estado=estado,
            fecha_limite=fecha_limite, creado_por=self.admin, sucursal=self.sucursal,
        )

    def api(self, user=None, token=None):
        client = APIClient()
        if token:
            client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        elif user:
            client.force_authenticate(user=user)
        return client

    # --- Permisos ---

    def test_requiere_autenticacion(self):
        response = self.api().get(self.base_url)
        self.assertIn(response.status_code, (401, 403))

    def test_admin_puede_leer(self):
        response = self.api(user=self.admin).get(self.base_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 3)

    def test_sucursal_puede_leer(self):
        response = self.api(token=self.sucursal_token).get(self.base_url)
        self.assertEqual(response.status_code, 200)

    def test_escritura_prohibida(self):
        # ReadOnlyModelViewSet: las escrituras se rechazan con 405 (Method Not
        # Allowed). Antes daba 403 porque EsSoloLectura bloqueaba por metodo;
        # con el permiso de lectura (PuedeLeerMaestro) el rechazo lo hace el
        # router por no existir la accion de escritura. Ambos cumplen "prohibida".
        client = self.api(user=self.admin)
        post = client.post(self.base_url, {}, format='json')
        patch = client.patch(f'{self.base_url}{self.cuenta_abierta.id}/', {}, format='json')
        delete = client.delete(f'{self.base_url}{self.cuenta_abierta.id}/')
        self.assertIn(post.status_code, (403, 405))
        self.assertIn(patch.status_code, (403, 405))
        self.assertIn(delete.status_code, (403, 405))

    # --- Forma de la respuesta ---

    def test_lista_incluye_campos_denormalizados(self):
        response = self.api(user=self.admin).get(f'{self.base_url}?estado=ABIERTA')
        fila = response.data['results'][0]
        for campo in (
            'numero_venta', 'cliente_id', 'cliente_nombre', 'cliente_cedula_rnc',
            'sucursal_codigo', 'metodo_plazo_nombre', 'total', 'monto_inicial',
            'saldo', 'estado', 'fecha_emision', 'fecha_limite', 'esta_vencida',
        ):
            self.assertIn(campo, fila)
        self.assertEqual(fila['numero_venta'], 'V-0001')
        self.assertEqual(fila['cliente_nombre'], 'Royal Plast SRL')
        self.assertEqual(fila['sucursal_codigo'], 'SD-001')

    def test_esta_vencida_se_calcula_por_fecha_no_por_estado(self):
        # Cuenta PARCIAL con fecha pasada -> esta_vencida=True aunque estado != VENCIDA.
        response = self.api(user=self.admin).get(
            f'{self.base_url}{self.cuenta_vencida_de_hecho.id}/'
        )
        self.assertEqual(response.data['estado'], 'PARCIAL')
        self.assertTrue(response.data['esta_vencida'])
        # Cuenta abierta a futuro -> no vencida.
        r2 = self.api(user=self.admin).get(f'{self.base_url}{self.cuenta_abierta.id}/')
        self.assertFalse(r2.data['esta_vencida'])

    def test_detalle_incluye_cuotas_y_pagos(self):
        CuotaCxC.objects.create(
            cuenta=self.cuenta_abierta, numero=1, monto=Decimal('1000.00'),
            saldo=Decimal('1000.00'), fecha_vencimiento=timezone.localdate(),
        )
        PagoCxC.objects.create(
            cuenta=self.cuenta_abierta, metodo='EFECTIVO', monto=Decimal('200.00'),
            registrado_por=self.admin,
        )
        response = self.api(user=self.admin).get(f'{self.base_url}{self.cuenta_abierta.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['cuotas']), 1)
        self.assertEqual(len(response.data['pagos']), 1)
        self.assertEqual(response.data['pagos'][0]['registrado_por'], 'admin_cxc')

    # --- Filtros ---

    def test_filtro_estado(self):
        response = self.api(user=self.admin).get(f'{self.base_url}?estado=PAGADA')
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['numero_venta'], 'V-0003')

    def test_filtro_vencidas(self):
        response = self.api(user=self.admin).get(f'{self.base_url}?vencidas=true')
        # Solo la PARCIAL con fecha pasada; la PAGADA queda fuera pese a fecha pasada.
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['numero_venta'], 'V-0002')

    def test_filtro_search_por_numero_venta(self):
        response = self.api(user=self.admin).get(f'{self.base_url}?search=V-0001')
        self.assertEqual(response.data['count'], 1)

    def test_filtro_search_por_cedula_rnc(self):
        response = self.api(user=self.admin).get(f'{self.base_url}?search=130123456')
        self.assertEqual(response.data['count'], 2)
        for fila in response.data['results']:
            self.assertEqual(fila['cliente_cedula_rnc'], '130123456')

    # --- Resumen ---

    def test_resumen_agregados(self):
        response = self.api(user=self.admin).get(self.resumen_url)
        self.assertEqual(response.status_code, 200)
        # Cartera viva = 1000 (abierta) + 800 (parcial). Pagada no cuenta.
        self.assertEqual(Decimal(str(response.data['cartera_total'])), Decimal('1800.00'))
        self.assertEqual(Decimal(str(response.data['saldo_vencido'])), Decimal('800.00'))
        self.assertEqual(response.data['cuentas_abiertas'], 2)
        self.assertEqual(response.data['cuentas_vencidas'], 1)
        self.assertEqual(response.data['clientes_con_saldo'], 1)

    def test_resumen_cartera_vacia_devuelve_ceros(self):
        CuentaPorCobrar.objects.all().delete()
        response = self.api(user=self.admin).get(self.resumen_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(str(response.data['cartera_total'])), Decimal('0'))
        self.assertEqual(response.data['cuentas_abiertas'], 0)
        self.assertEqual(response.data['clientes_con_saldo'], 0)
