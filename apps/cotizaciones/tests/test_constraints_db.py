"""
COT-008 / COT-015 (constraints de BD): una cotizacion es fuente autorizada de
precio para la venta, asi que importes/cantidades imposibles y el vinculo
estado<->venta se garantizan a nivel de BD, no solo en la app.

Se fija `numero_cotizacion` explicito para saltar la numeracion automatica.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.clientes.models import Cliente
from apps.cotizaciones.models import Cotizacion, DetalleCotizacion
from apps.productos.models import Categoria, Producto


class CotizacionDBConstraintsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='dbc_cot', email='dbc_cot@test.local', password='x',
            rol='ADMIN', activo=True,
        )
        self.cliente = Cliente.objects.create(nombre='DBC Cot', tipo='CORPORATIVO')
        self.categoria = Categoria.objects.create(nombre='DBC Cot')
        self.producto = Producto.objects.create(
            sku='DBC-COT-1', codigo_barras='DBC-COT-1', nombre='P', descripcion='',
            categoria=self.categoria, precio_venta=Decimal('10.00'),
            stock_minimo=1, activo=True, estado='nuevo', marca='', atributos={},
        )
        self.cotizacion = Cotizacion.objects.create(
            numero_cotizacion='DBC-COT-OK-1', cliente=self.cliente,
            usuario=self.user, fecha_creacion=timezone.now(), total=Decimal('10.00'),
        )

    def _rechaza(self, crear):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                crear()

    # -- COT-015: estado CONVERTIDA exige venta ------------------------------

    def test_convertida_sin_venta_rechazada(self):
        self._rechaza(lambda: Cotizacion.objects.create(
            numero_cotizacion='DBC-COT-BAD-1', cliente=self.cliente,
            usuario=self.user, fecha_creacion=timezone.now(),
            total=Decimal('10.00'), estado='CONVERTIDA',
        ))

    def test_convertida_con_venta_se_acepta(self):
        from apps.ventas.models import Venta

        venta = Venta.objects.create(
            numero_venta='DBC-COT-V-1', usuario=self.user,
            subtotal=Decimal('10.00'), total=Decimal('10.00'), estado='COMPLETADA',
        )
        self.cotizacion.estado = 'CONVERTIDA'
        self.cotizacion.venta = venta
        self.cotizacion.save(update_fields=['estado', 'venta'])
        self.cotizacion.refresh_from_db()
        self.assertEqual(self.cotizacion.estado, 'CONVERTIDA')

    def test_pendiente_con_venta_rechazada(self):
        from apps.ventas.models import Venta

        venta = Venta.objects.create(
            numero_venta='DBC-COT-V-2', usuario=self.user,
            subtotal=Decimal('10.00'), total=Decimal('10.00'), estado='COMPLETADA',
        )
        self._rechaza(lambda: Cotizacion.objects.create(
            numero_cotizacion='DBC-COT-BAD-PEND', cliente=self.cliente,
            usuario=self.user, fecha_creacion=timezone.now(),
            total=Decimal('10.00'), estado='PENDIENTE', venta=venta,
        ))

    def test_numero_legacy_duplicado_rechazado(self):
        self._rechaza(lambda: Cotizacion.objects.create(
            numero_cotizacion=self.cotizacion.numero_cotizacion,
            cliente=self.cliente, usuario=self.user,
            fecha_creacion=timezone.now(), total=Decimal('10.00'),
        ))

    # -- COT-008: importes imposibles ----------------------------------------

    def test_cotizacion_total_negativo_rechazada(self):
        self._rechaza(lambda: Cotizacion.objects.create(
            numero_cotizacion='DBC-COT-BAD-2', cliente=self.cliente,
            usuario=self.user, fecha_creacion=timezone.now(),
            total=Decimal('-1.00'),
        ))

    def test_detalle_cantidad_cero_rechazada(self):
        self._rechaza(lambda: DetalleCotizacion.objects.create(
            cotizacion=self.cotizacion, producto=self.producto, cantidad=0,
            precio_unitario=Decimal('10.00'), descuento_monto=Decimal('0.00'),
        ))

    def test_detalle_precio_cero_rechazado(self):
        self._rechaza(lambda: DetalleCotizacion.objects.create(
            cotizacion=self.cotizacion, producto=self.producto, cantidad=1,
            precio_unitario=Decimal('0.00'), descuento_monto=Decimal('0.00'),
        ))

    def test_detalle_descuento_supera_subtotal_rechazado(self):
        self._rechaza(lambda: DetalleCotizacion.objects.create(
            cotizacion=self.cotizacion, producto=self.producto, cantidad=1,
            precio_unitario=Decimal('10.00'), descuento_monto=Decimal('20.00'),
        ))

    def test_detalle_valido_se_acepta(self):
        detalle = DetalleCotizacion.objects.create(
            cotizacion=self.cotizacion, producto=self.producto, cantidad=2,
            precio_unitario=Decimal('10.00'), descuento_monto=Decimal('5.00'),
        )
        self.assertEqual(detalle.subtotal, Decimal('20.00'))
        self.assertEqual(detalle.total_linea, Decimal('15.00'))

    def test_editar_y_borrar_detalle_reconcilia_la_cabecera(self):
        detalle = DetalleCotizacion.objects.create(
            cotizacion=self.cotizacion, producto=self.producto, cantidad=2,
            precio_unitario=Decimal('10.00'), descuento_monto=Decimal('5.00'),
        )
        self.cotizacion.refresh_from_db()
        self.assertEqual(self.cotizacion.total, Decimal('15.00'))

        detalle.cantidad = 3
        detalle.save()
        self.cotizacion.refresh_from_db()
        self.assertEqual(self.cotizacion.subtotal, Decimal('30.00'))
        self.assertEqual(self.cotizacion.total, Decimal('25.00'))

        detalle.delete()
        self.cotizacion.refresh_from_db()
        self.assertEqual(self.cotizacion.total, Decimal('0.00'))
