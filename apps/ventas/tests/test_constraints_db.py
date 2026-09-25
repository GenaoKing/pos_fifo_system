"""
DB-CONSTRAINTS (ventas): las restricciones CHECK rechazan importes y cantidades
imposibles a nivel de BD, no solo en la app.

Cada caso escribe DIRECTAMENTE con `objects.create()` — que NO corre
`full_clean()`, asi que los validators de campo no intervienen — y espera
`IntegrityError`. Es la escritura que un script, un shell o una replicacion mal
formada podrian intentar; la BD la rechaza igual.

Se fija `numero_venta` explicito para saltar el bucle de reintento de
correlativo de `Venta.save()` (que atrapa IntegrityError creyendo que es una
colision de numero); asi la violacion del CHECK sale directa.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.productos.models import Categoria, Producto
from apps.ventas.models import DetalleVenta, Pago, Venta


class VentasDBConstraintsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='dbc_ventas', email='dbc_ventas@test.local', password='x',
            rol='ADMIN', activo=True,
        )
        self.categoria = Categoria.objects.create(nombre='DBC Ventas')
        self.producto = Producto.objects.create(
            sku='DBC-V-1', codigo_barras='DBC-V-1', nombre='P', descripcion='',
            categoria=self.categoria, precio_venta=Decimal('10.00'),
            stock_minimo=1, activo=True, estado='nuevo', marca='', atributos={},
        )
        self.venta = Venta.objects.create(
            numero_venta='DBC-V-OK-1', usuario=self.user,
            subtotal=Decimal('10.00'), total=Decimal('10.00'), estado='COMPLETADA',
        )

    def _rechaza(self, crear):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                crear()

    # -- Venta ---------------------------------------------------------------

    def test_venta_total_no_positivo_rechazada(self):
        self._rechaza(lambda: Venta.objects.create(
            numero_venta='DBC-V-BAD-1', usuario=self.user,
            subtotal=Decimal('0.00'), total=Decimal('0.00'), estado='COMPLETADA',
        ))

    def test_venta_subtotal_negativo_rechazada(self):
        self._rechaza(lambda: Venta.objects.create(
            numero_venta='DBC-V-BAD-2', usuario=self.user,
            subtotal=Decimal('-1.00'), total=Decimal('5.00'), estado='COMPLETADA',
        ))

    def test_venta_descuento_negativo_rechazada(self):
        self._rechaza(lambda: Venta.objects.create(
            numero_venta='DBC-V-BAD-3', usuario=self.user,
            subtotal=Decimal('10.00'), descuento_total=Decimal('-1.00'),
            total=Decimal('10.00'), estado='COMPLETADA',
        ))

    # -- DetalleVenta --------------------------------------------------------

    def test_detalle_cantidad_cero_rechazada(self):
        self._rechaza(lambda: DetalleVenta.objects.create(
            venta=self.venta, producto=self.producto, cantidad=0,
            precio_unitario=Decimal('10.00'), descuento_monto=Decimal('0.00'),
        ))

    def test_detalle_precio_cero_rechazado(self):
        self._rechaza(lambda: DetalleVenta.objects.create(
            venta=self.venta, producto=self.producto, cantidad=1,
            precio_unitario=Decimal('0.00'), descuento_monto=Decimal('0.00'),
        ))

    def test_detalle_descuento_negativo_rechazado(self):
        self._rechaza(lambda: DetalleVenta.objects.create(
            venta=self.venta, producto=self.producto, cantidad=1,
            precio_unitario=Decimal('10.00'), descuento_monto=Decimal('-1.00'),
        ))

    def test_detalle_descuento_supera_subtotal_rechazado(self):
        # subtotal = 1 * 10 = 10; descuento 20 lo supera (y dejaria total < 0).
        self._rechaza(lambda: DetalleVenta.objects.create(
            venta=self.venta, producto=self.producto, cantidad=1,
            precio_unitario=Decimal('10.00'), descuento_monto=Decimal('20.00'),
        ))

    def test_detalle_valido_se_acepta(self):
        detalle = DetalleVenta.objects.create(
            venta=self.venta, producto=self.producto, cantidad=2,
            precio_unitario=Decimal('10.00'), descuento_monto=Decimal('5.00'),
        )
        self.assertEqual(detalle.subtotal, Decimal('20.00'))
        self.assertEqual(detalle.total_linea, Decimal('15.00'))

    # -- Pago ----------------------------------------------------------------

    def test_pago_monto_cero_rechazado(self):
        self._rechaza(lambda: Pago.objects.create(
            venta=self.venta, metodo='EFECTIVO', monto=Decimal('0.00'),
        ))

    def test_pago_valido_se_acepta(self):
        pago = Pago.objects.create(
            venta=self.venta, metodo='EFECTIVO', monto=Decimal('10.00'),
        )
        self.assertEqual(pago.monto, Decimal('10.00'))
