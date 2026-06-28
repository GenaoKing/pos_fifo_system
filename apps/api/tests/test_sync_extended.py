from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.api.views.sync import (
    _handler_cotizacion_convertida,
    _handler_cotizacion_creada,
    _handler_inventario_snapshot,
    _handler_movimiento_inventario,
)
from apps.clientes.models import Cliente
from apps.configuracion.models import ConfiguracionNegocio
from apps.cotizaciones.models import Cotizacion
from apps.cuentas_por_cobrar.models import MetodoPlazoCredito
from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal
from apps.sync.models import InventarioMovimientoSync, InventarioSucursalSnapshot
from apps.ventas.models import Venta

User = get_user_model()


class SyncExtendedEndpointTests(TestCase):
    def setUp(self):
        self.svc = User.objects.create_user('svc_sync_ext', 'svc@test.local', 'x', rol='CAJERA')
        self.sucursal = Sucursal.objects.create(
            codigo='SD-001',
            nombre='Sucursal SD',
            activa=True,
            usuario_servicio=self.svc,
        )
        self.token = Token.objects.create(user=self.svc)

    def _api(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        return client

    def test_heartbeat_actualiza_ultima_sync(self):
        self.assertIsNone(self.sucursal.ultima_sync)

        res = self._api().post('/api/v1/sync/heartbeat/', {'timestamp': timezone.now().isoformat()})

        self.assertEqual(res.status_code, 200)
        self.sucursal.refresh_from_db()
        self.assertIsNotNone(self.sucursal.ultima_sync)

    def test_metodos_credito_endpoint_filtra_global_y_sucursal(self):
        otra = Sucursal.objects.create(codigo='STI-001', nombre='STI', activa=True)
        MetodoPlazoCredito.objects.create(nombre='Global 30', activo=True)
        MetodoPlazoCredito.objects.create(nombre='Sucursal SD', sucursal=self.sucursal, activo=True)
        MetodoPlazoCredito.objects.create(nombre='Sucursal STI', sucursal=otra, activo=True)

        res = self._api().get('/api/v1/sync/metodos-credito/')

        self.assertEqual(res.status_code, 200)
        nombres = {row['nombre'] for row in res.data}
        self.assertIn('Global 30', nombres)
        self.assertIn('Sucursal SD', nombres)
        self.assertNotIn('Sucursal STI', nombres)

    def test_configuracion_endpoint_expone_allowlist_sin_hardware(self):
        ConfiguracionNegocio.objects.create(
            sucursal=self.sucursal,
            nombre_negocio='Royal Plast',
            pago_tarjeta=True,
            permitir_inventario_negativo=True,
        )

        res = self._api().get('/api/v1/sync/configuracion/')

        self.assertEqual(res.status_code, 200)
        row = res.data[0]
        self.assertEqual(row['nombre_negocio'], 'Royal Plast')
        self.assertTrue(row['pago_tarjeta'])
        self.assertNotIn('nombre_impresora_termica', row)
        self.assertNotIn('nombre_impresora_zebra', row)


class SyncExtendedHandlersTests(TestCase):
    def setUp(self):
        self.svc = User.objects.create_user('svc_handlers', 'h@test.local', 'x', rol='CAJERA')
        self.sucursal = Sucursal.objects.create(
            codigo='SD-001',
            nombre='Sucursal SD',
            activa=True,
            usuario_servicio=self.svc,
        )
        self.categoria = Categoria.objects.create(nombre='Plasticos')
        self.producto = Producto.objects.create(
            sku='SKU-001',
            nombre='Vaso',
            categoria=self.categoria,
            precio_venta=Decimal('25.00'),
            stock_minimo=5,
        )
        self.cliente = Cliente.objects.create(
            tipo='PERSONAL',
            nombre='Cliente Sync',
            cedula_rnc='00112345678',
        )

    def test_handler_movimiento_inventario_es_idempotente_por_movimiento_local(self):
        payload = {
            'movimiento_id_local': 10,
            'tipo': 'COMPRA',
            'producto_sku': 'SKU-001',
            'producto_nombre': 'Vaso',
            'lote_numero': 'LOTE-1',
            'cantidad': 4,
            'cantidad_anterior': 0,
            'cantidad_nueva': 4,
            'costo_unitario': '10.00',
            'referencia_tipo': 'Compra',
            'referencia_id': 99,
            'usuario_username': self.svc.username,
            'fecha_movimiento': timezone.now().isoformat(),
        }

        _handler_movimiento_inventario(self.sucursal, payload)
        _handler_movimiento_inventario(self.sucursal, {**payload, 'cantidad_nueva': 5})

        self.assertEqual(InventarioMovimientoSync.objects.count(), 1)
        mov = InventarioMovimientoSync.objects.get()
        self.assertEqual(mov.cantidad_nueva, 5)

    def test_handler_snapshot_hace_upsert_por_sucursal_y_sku(self):
        payload = {
            'sucursal_codigo': 'SD-001',
            'timestamp': timezone.now().isoformat(),
            'items': [
                {
                    'producto_sku': 'SKU-001',
                    'producto_nombre': 'Vaso',
                    'stock_actual': 4,
                    'stock_minimo': 5,
                    'bajo_stock': True,
                    'valor_fifo': '40.00',
                }
            ],
        }

        _handler_inventario_snapshot(self.sucursal, payload)
        payload['items'][0]['stock_actual'] = 7
        payload['items'][0]['bajo_stock'] = False
        _handler_inventario_snapshot(self.sucursal, payload)

        self.assertEqual(InventarioSucursalSnapshot.objects.count(), 1)
        snap = InventarioSucursalSnapshot.objects.get()
        self.assertEqual(snap.stock_actual, 7)
        self.assertFalse(snap.bajo_stock)

    def test_handler_cotizacion_crea_y_convierte_cuando_venta_existe(self):
        payload = {
            'cotizacion_id_local': 1,
            'numero_cotizacion': 'SD-001-COT-20260628-00001',
            'sucursal_codigo': 'SD-001',
            'cliente_cedula_rnc': self.cliente.cedula_rnc,
            'cliente_nombre': self.cliente.nombre,
            'usuario_username': self.svc.username,
            'fecha_creacion': timezone.now().isoformat(),
            'subtotal': '25.00',
            'descuento_total': '0.00',
            'total': '25.00',
            'estado': 'PENDIENTE',
            'venta_numero': None,
            'notas': '',
            'detalles': [
                {
                    'producto_sku': 'SKU-001',
                    'producto_nombre': 'Vaso',
                    'cantidad': 1,
                    'precio_unitario': '25.00',
                    'subtotal': '25.00',
                    'descuento_monto': '0.00',
                    'descuento_porcentaje': '0.00',
                    'total_linea': '25.00',
                }
            ],
        }
        _handler_cotizacion_creada(self.sucursal, payload)

        venta = Venta.objects.create(
            numero_venta='SD-001-V20260628-0001',
            sucursal=self.sucursal,
            usuario=self.svc,
            cliente=self.cliente,
            fecha_venta=timezone.now(),
            subtotal=Decimal('25.00'),
            total=Decimal('25.00'),
        )
        _handler_cotizacion_convertida(
            self.sucursal,
            {**payload, 'estado': 'CONVERTIDA', 'venta_numero': venta.numero_venta},
        )

        cotizacion = Cotizacion.objects.get(numero_cotizacion=payload['numero_cotizacion'])
        self.assertEqual(cotizacion.estado, 'CONVERTIDA')
        self.assertEqual(cotizacion.venta, venta)
