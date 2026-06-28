from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.clientes.models import Cliente
from apps.cotizaciones.models import Cotizacion, DetalleCotizacion
from apps.inventario.models import AjusteInventario, Compra, DetalleCompra
from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal
from apps.sync import serializers

User = get_user_model()


class ExtendedSyncSerializersTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('sync_user', 's@test.local', 'x', rol='CAJERA')
        self.sucursal = Sucursal.objects.create(
            codigo='SD-001', nombre='Sucursal SD', activa=True, usuario_servicio=self.user
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

    def _compra_con_lote(self):
        compra = Compra.objects.create(
            proveedor='Proveedor',
            usuario=self.user,
            sucursal=self.sucursal,
            total=Decimal('100.00'),
        )
        DetalleCompra.objects.create(
            compra=compra,
            producto=self.producto,
            cantidad=4,
            costo_unitario=Decimal('10.00'),
            subtotal=Decimal('40.00'),
        )
        return compra

    def test_serializar_ajuste_usa_lote_producto_y_fecha_real(self):
        compra = self._compra_con_lote()
        lote = compra.detalles.first().lote
        ajuste = AjusteInventario.objects.create(
            lote=lote,
            tipo='MERMA',
            cantidad=-1,
            motivo='Rotura',
            usuario=self.user,
        )

        payload = serializers.serializar_ajuste_inventario(ajuste)

        self.assertEqual(payload['sucursal_codigo'], 'SD-001')
        self.assertEqual(payload['producto_sku'], 'SKU-001')
        self.assertEqual(payload['lote_numero'], lote.numero_lote)
        self.assertEqual(payload['fecha'], ajuste.fecha_ajuste.isoformat())

    @override_settings(SUCURSAL_CODIGO='SD-001')
    def test_serializar_snapshot_incluye_stock_y_valor_fifo(self):
        self._compra_con_lote()

        payload = serializers.serializar_inventario_snapshot()

        self.assertEqual(payload['sucursal_codigo'], 'SD-001')
        item = payload['items'][0]
        self.assertEqual(item['producto_sku'], 'SKU-001')
        self.assertEqual(item['stock_actual'], 4)
        self.assertEqual(item['valor_fifo'], '40.00')

    def test_serializar_cotizacion_incluye_detalles_y_venta_numero(self):
        cotizacion = Cotizacion.objects.create(
            cliente=self.cliente,
            usuario=self.user,
            sucursal=self.sucursal,
            total=Decimal('25.00'),
        )
        DetalleCotizacion.objects.create(
            cotizacion=cotizacion,
            producto=self.producto,
            cantidad=1,
            precio_unitario=Decimal('25.00'),
            subtotal=Decimal('25.00'),
            descuento_monto=Decimal('0.00'),
            total_linea=Decimal('25.00'),
        )

        payload = serializers.serializar_cotizacion(cotizacion)

        self.assertEqual(payload['sucursal_codigo'], 'SD-001')
        self.assertEqual(payload['cliente_cedula_rnc'], '00112345678')
        self.assertEqual(payload['detalles'][0]['producto_sku'], 'SKU-001')
