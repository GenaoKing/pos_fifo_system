from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.cuentas_por_cobrar.models import CuentaPorCobrar, MetodoPlazoCredito, PagoCxC
from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal
from apps.ventas.models import DetalleVenta, Pago, Venta


class ReportesCloudTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='admin',
            email='admin@example.com',
            password='pass',
            rol='ADMIN',
            activo=True,
        )
        self.cajera = User.objects.create_user(
            username='santiago',
            email='santiago@example.com',
            password='pass',
            first_name='Santiago',
            rol='CAJERA',
            activo=True,
        )
        self.no_admin = User.objects.create_user(
            username='operador',
            email='operador@example.com',
            password='pass',
            rol='CAJERA',
            activo=True,
        )
        self.sucursal_sd = Sucursal.objects.create(
            codigo='SD-001',
            nombre='Sucursal Principal',
            activa=True,
            ultima_sync=timezone.now() - timedelta(minutes=2),
        )
        self.sucursal_sti = Sucursal.objects.create(
            codigo='STI-001',
            nombre='Sucursal Santiago',
            activa=True,
            ultima_sync=None,
        )
        self.categoria = Categoria.objects.create(nombre='General')
        self.producto_a = Producto.objects.create(
            sku='PROD-0001',
            codigo_barras='BAR-0001',
            nombre='Producto A',
            categoria=self.categoria,
            precio_venta=Decimal('100.00'),
            stock_minimo=1,
        )
        self.producto_b = Producto.objects.create(
            sku='PROD-0002',
            codigo_barras='BAR-0002',
            nombre='Producto B',
            categoria=self.categoria,
            precio_venta=Decimal('50.00'),
            stock_minimo=1,
        )
        self.cliente = Cliente.objects.create(
            nombre='Cliente Credito',
            cedula_rnc='00100000001',
            limite_credito=Decimal('5000.00'),
        )
        self.metodo_plazo = MetodoPlazoCredito.objects.create(nombre='30 dias')
        self.today = timezone.localdate()
        self.now_local = timezone.make_aware(
            datetime.combine(self.today, time(10, 0)),
            timezone.get_current_timezone(),
        )

        self.venta_contado = self._crear_venta(
            sucursal=self.sucursal_sd,
            total=Decimal('1000.00'),
            condicion_pago='CONTADO',
        )
        self._crear_detalle(self.venta_contado, self.producto_a, 2, Decimal('500.00'))
        Pago.objects.create(venta=self.venta_contado, metodo='EFECTIVO', monto=Decimal('600.00'))
        Pago.objects.create(venta=self.venta_contado, metodo='TARJETA', monto=Decimal('400.00'))

        self.venta_credito = self._crear_venta(
            sucursal=self.sucursal_sd,
            total=Decimal('500.00'),
            condicion_pago='CREDITO',
            cliente=self.cliente,
        )
        self._crear_detalle(self.venta_credito, self.producto_b, 1, Decimal('500.00'))
        Pago.objects.create(venta=self.venta_credito, metodo='TRANSFERENCIA', monto=Decimal('100.00'))
        Pago.objects.create(venta=self.venta_credito, metodo='CREDITO', monto=Decimal('400.00'))
        self.cuenta = CuentaPorCobrar.objects.create(
            cliente=self.cliente,
            venta=self.venta_credito,
            metodo_plazo=self.metodo_plazo,
            total=Decimal('500.00'),
            monto_inicial=Decimal('100.00'),
            saldo=Decimal('400.00'),
            fecha_emision=self.today,
            fecha_limite=self.today + timedelta(days=30),
            creado_por=self.cajera,
            sucursal=self.sucursal_sd,
        )
        PagoCxC.objects.create(
            cuenta=self.cuenta,
            metodo=PagoCxC.METODO_EFECTIVO,
            monto=Decimal('120.00'),
            registrado_por=self.cajera,
            fecha_pago=self.now_local,
        )

        self.venta_legacy = self._crear_venta(
            sucursal=None,
            total=Decimal('99.00'),
            condicion_pago='CONTADO',
        )

    def _api(self, user=None):
        client = APIClient()
        if user:
            client.force_authenticate(user=user)
        return client

    def _crear_venta(self, sucursal, total, condicion_pago='CONTADO', cliente=None, estado='COMPLETADA'):
        return Venta.objects.create(
            fecha_venta=self.now_local,
            usuario=self.cajera,
            cliente=cliente,
            sucursal=sucursal,
            subtotal=total,
            total=total,
            condicion_pago=condicion_pago,
            estado=estado,
        )

    def _crear_detalle(self, venta, producto, cantidad, precio_unitario):
        return DetalleVenta.objects.create(
            venta=venta,
            producto=producto,
            cantidad=cantidad,
            precio_unitario=precio_unitario,
            descuento_monto=Decimal('0.00'),
            costo_fifo=Decimal('0.00'),
        )

    def test_reportes_requieren_admin(self):
        anon_response = self._api().get('/api/v1/reportes/comparativo/')
        cajera_response = self._api(self.no_admin).get('/api/v1/reportes/comparativo/')

        self.assertIn(anon_response.status_code, (401, 403))
        self.assertEqual(cajera_response.status_code, 403)

    def test_comparativo_multi_sucursal_excluye_legacy_y_separa_cxc(self):
        response = self._api(self.admin).get(
            '/api/v1/reportes/comparativo/',
            {
                'desde': str(self.today),
                'hasta': str(self.today),
                'agrupacion': 'dia',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['metadata']['legacy_ventas_omitidas'], 1)
        self.assertEqual(len(response.data['sucursales']), 2)

        sd = next(s for s in response.data['sucursales'] if s['sucursal_codigo'] == 'SD-001')
        sti = next(s for s in response.data['sucursales'] if s['sucursal_codigo'] == 'STI-001')

        self.assertNotEqual(sd['sucursal_codigo'], 'LOCAL')
        self.assertEqual(sd['totales']['cantidad_ventas'], 2)
        self.assertEqual(sd['totales']['ventas_facturadas'], '1500.00')
        self.assertEqual(sd['totales']['credito_facturado'], '500.00')
        self.assertEqual(sd['totales']['cobros_cxc'], '120.00')
        self.assertEqual(sd['serie'][0]['periodo'], str(self.today))
        self.assertEqual(sti['totales']['ventas_facturadas'], '0.00')

        # Totales globales completos (contrato que consume el portal).
        self.assertEqual(response.data['totales']['cantidad_ventas'], 2)
        self.assertEqual(response.data['totales']['ventas_facturadas'], '1500.00')
        self.assertEqual(response.data['totales']['credito_facturado'], '500.00')
        self.assertEqual(response.data['totales']['cobros_cxc'], '120.00')
        self.assertEqual(response.data['totales']['ticket_promedio'], '750.00')

        # Zero-fill: la sucursal sin movimiento trae la serie completa en cero.
        self.assertEqual(len(sti['serie']), 1)
        self.assertEqual(sti['serie'][0]['periodo'], str(self.today))
        self.assertEqual(sti['serie'][0]['cantidad_ventas'], 0)
        self.assertEqual(sti['serie'][0]['ventas_facturadas'], '0.00')

    def test_comparativo_filtra_sucursal_y_valida_parametros(self):
        response = self._api(self.admin).get(
            '/api/v1/reportes/comparativo/',
            {'desde': str(self.today), 'hasta': str(self.today), 'sucursal': 'SD-001'},
        )
        invalid_sucursal = self._api(self.admin).get(
            '/api/v1/reportes/comparativo/',
            {'desde': str(self.today), 'hasta': str(self.today), 'sucursal': 'NOPE'},
        )
        invalid_date = self._api(self.admin).get(
            '/api/v1/reportes/comparativo/',
            {'desde': '2026-99-99', 'hasta': str(self.today)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['sucursales']), 1)
        self.assertEqual(invalid_sucursal.status_code, 404)
        self.assertEqual(invalid_date.status_code, 400)

    def test_comparativo_zero_fill_dia_completa_periodos_sin_movimiento(self):
        desde = self.today - timedelta(days=2)
        response = self._api(self.admin).get(
            '/api/v1/reportes/comparativo/',
            {'desde': str(desde), 'hasta': str(self.today), 'agrupacion': 'dia'},
        )

        self.assertEqual(response.status_code, 200)
        sd = next(s for s in response.data['sucursales'] if s['sucursal_codigo'] == 'SD-001')
        sti = next(s for s in response.data['sucursales'] if s['sucursal_codigo'] == 'STI-001')

        expected_periodos = [str(desde + timedelta(days=i)) for i in range(3)]
        self.assertEqual([p['periodo'] for p in sd['serie']], expected_periodos)
        self.assertEqual([p['periodo'] for p in sti['serie']], expected_periodos)

        # Los dos primeros dias de SD-001 sin movimiento, el de hoy con datos.
        self.assertEqual(sd['serie'][0]['cantidad_ventas'], 0)
        self.assertEqual(sd['serie'][0]['ventas_facturadas'], '0.00')
        self.assertEqual(sd['serie'][1]['cantidad_ventas'], 0)
        self.assertEqual(sd['serie'][2]['cantidad_ventas'], 2)
        self.assertEqual(sd['serie'][2]['ventas_facturadas'], '1500.00')
        self.assertEqual(sd['serie'][2]['cobros_cxc'], '120.00')

        # STI-001 todo en cero.
        for punto in sti['serie']:
            self.assertEqual(punto['cantidad_ventas'], 0)
            self.assertEqual(punto['ventas_facturadas'], '0.00')

    def test_comparativo_zero_fill_semana_genera_lunes_consecutivos(self):
        # Rango fijo en el pasado (sin ventas): 2025-01-02 es jueves, asi que
        # la primera clave es el lunes ISO anterior al rango (2024-12-30) —
        # misma semantica que _period_key.
        response = self._api(self.admin).get(
            '/api/v1/reportes/comparativo/',
            {'desde': '2025-01-02', 'hasta': '2025-01-21', 'agrupacion': 'semana'},
        )

        self.assertEqual(response.status_code, 200)
        sd = next(s for s in response.data['sucursales'] if s['sucursal_codigo'] == 'SD-001')
        self.assertEqual(
            [p['periodo'] for p in sd['serie']],
            ['2024-12-30', '2025-01-06', '2025-01-13', '2025-01-20'],
        )
        for punto in sd['serie']:
            self.assertEqual(punto['ventas_facturadas'], '0.00')

    def test_comparativo_zero_fill_mes_genera_meses_consecutivos(self):
        response = self._api(self.admin).get(
            '/api/v1/reportes/comparativo/',
            {'desde': '2025-01-15', 'hasta': '2025-03-10', 'agrupacion': 'mes'},
        )

        self.assertEqual(response.status_code, 200)
        sd = next(s for s in response.data['sucursales'] if s['sucursal_codigo'] == 'SD-001')
        self.assertEqual(
            [p['periodo'] for p in sd['serie']],
            ['2025-01', '2025-02', '2025-03'],
        )

    def test_comparativo_rechaza_rango_que_excede_max_puntos(self):
        # 2 anios por dia: ~731 puntos > 400 -> 400 con mensaje accionable.
        rechazado = self._api(self.admin).get(
            '/api/v1/reportes/comparativo/',
            {'desde': '2024-01-01', 'hasta': '2026-01-01', 'agrupacion': 'dia'},
        )
        # El mismo rango agrupado por mes cabe de sobra -> 200.
        aceptado = self._api(self.admin).get(
            '/api/v1/reportes/comparativo/',
            {'desde': '2024-01-01', 'hasta': '2026-01-01', 'agrupacion': 'mes'},
        )

        self.assertEqual(rechazado.status_code, 400)
        self.assertIn('agrupacion', rechazado.data['error'])
        self.assertEqual(aceptado.status_code, 200)
        sd = next(s for s in aceptado.data['sucursales'] if s['sucursal_codigo'] == 'SD-001')
        self.assertEqual(len(sd['serie']), 25)

    def test_ventas_por_cajero_separa_pagos_ventas_y_cxc(self):
        response = self._api(self.admin).get(
            '/api/v1/reportes/ventas-por-cajero/',
            {'desde': str(self.today), 'hasta': str(self.today)},
        )

        self.assertEqual(response.status_code, 200)
        cajero = response.data['cajeros'][0]
        self.assertEqual(cajero['username'], 'santiago')
        self.assertEqual(cajero['cantidad_ventas'], 2)
        self.assertEqual(cajero['ventas_facturadas'], '1500.00')
        self.assertEqual(cajero['total_efectivo'], '600.00')
        self.assertEqual(cajero['total_transferencia'], '100.00')
        self.assertEqual(cajero['total_tarjeta'], '400.00')
        self.assertEqual(cajero['credito_facturado'], '500.00')
        self.assertEqual(cajero['cobros_cxc'], '120.00')

    def test_top_productos_agrupa_por_producto_y_respeta_limit(self):
        response = self._api(self.admin).get(
            '/api/v1/reportes/top-productos/',
            {'desde': str(self.today), 'hasta': str(self.today), 'limit': 1},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['productos']), 1)
        producto = response.data['productos'][0]
        self.assertEqual(producto['sku'], 'PROD-0001')
        self.assertEqual(producto['cantidad_vendida'], '2.00')
        self.assertEqual(producto['ventas_facturadas'], '1000.00')
        self.assertEqual(producto['transacciones'], 1)

    def test_cierre_consolidado_separa_credito_cxc_y_flujo(self):
        venta_anulada = self._crear_venta(
            sucursal=self.sucursal_sd,
            total=Decimal('75.00'),
            estado='ANULADA',
        )
        venta_anulada.fecha_anulacion = self.now_local
        venta_anulada.save(update_fields=['fecha_anulacion'])

        response = self._api(self.admin).get(
            '/api/v1/reportes/cierre-consolidado/',
            {'fecha': str(self.today)},
        )

        self.assertEqual(response.status_code, 200)
        sd = next(s for s in response.data['sucursales'] if s['sucursal_codigo'] == 'SD-001')
        self.assertEqual(sd['ventas_facturadas'], '1500.00')
        self.assertEqual(sd['credito_facturado'], '500.00')
        self.assertEqual(sd['cobros_cxc'], '120.00')
        self.assertEqual(sd['total_efectivo'], '600.00')
        self.assertEqual(sd['cantidad_anulaciones'], 1)
        self.assertEqual(sd['total_anulaciones'], '75.00')
        self.assertEqual(response.data['totales']['ventas_facturadas'], '1500.00')

    def test_ventas_hoy_conserva_contrato_y_agrega_metadata(self):
        response = self._api(self.admin).get('/api/v1/reportes/ventas-hoy/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('total_ventas', response.data['totales'])
        self.assertIn('ventas_facturadas', response.data['totales'])
        self.assertEqual(response.data['metadata']['legacy_ventas_omitidas'], 1)
        sd = next(s for s in response.data['sucursales'] if s['sucursal_codigo'] == 'SD-001')
        self.assertEqual(sd['total_ventas'], '1500.00')
        self.assertEqual(sd['credito_facturado'], '500.00')
        self.assertEqual(sd['cobros_cxc'], '120.00')

    def test_inventario_consolidado_mantiene_stock_por_sucursal(self):
        response = self._api(self.admin).get('/api/v1/reportes/inventario-consolidado/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('productos', response.data)
        self.assertIn('stock_por_sucursal', response.data['productos'][0])
        self.assertIn('ultima_actualizacion_por_sucursal', response.data)
        self.assertIn('sucursales_sin_datos', response.data)
