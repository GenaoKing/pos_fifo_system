"""
DB-CONSTRAINTS (inventario): las restricciones CHECK rechazan cantidades y
costos imposibles a nivel de BD. `cantidad_actual` del lote NO se restringe: el
inventario negativo es politica valida.

Se fijan `numero_compra`/`numero_lote` explicitos para saltar el bucle de
reintento de correlativo (`_guardar_con_correlativo`) que atrapa IntegrityError.
"""
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.inventario.models import Compra, DetalleCompra, Lote
from apps.productos.models import Categoria, Producto


class InventarioDBConstraintsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='dbc_inv', email='dbc_inv@test.local', password='x',
            rol='ADMIN', activo=True,
        )
        self.categoria = Categoria.objects.create(nombre='DBC Inv')
        self.producto = Producto.objects.create(
            sku='DBC-I-1', codigo_barras='DBC-I-1', nombre='P', descripcion='',
            categoria=self.categoria, precio_venta=Decimal('10.00'),
            stock_minimo=1, activo=True, estado='nuevo', marca='', atributos={},
        )
        self.compra = Compra.objects.create(
            numero_compra='DBC-C-OK-1', usuario=self.user, proveedor='Prov',
            total=Decimal('40.00'),
        )

    def _rechaza(self, crear):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                crear()

    # -- Compra --------------------------------------------------------------

    def test_compra_total_negativo_rechazada(self):
        self._rechaza(lambda: Compra.objects.create(
            numero_compra='DBC-C-BAD-1', usuario=self.user, proveedor='Prov',
            total=Decimal('-1.00'),
        ))

    def test_compra_total_cero_se_acepta(self):
        # La cabecera se crea con total 0 y se completa tras los detalles.
        compra = Compra.objects.create(
            numero_compra='DBC-C-OK-2', usuario=self.user, proveedor='Prov',
            total=Decimal('0.00'),
        )
        self.assertEqual(compra.total, Decimal('0.00'))

    # -- DetalleCompra -------------------------------------------------------

    def test_detalle_cantidad_cero_rechazada(self):
        self._rechaza(lambda: DetalleCompra.objects.create(
            compra=self.compra, producto=self.producto, cantidad=0,
            costo_unitario=Decimal('40.00'),
        ))

    def test_detalle_costo_cero_rechazado(self):
        self._rechaza(lambda: DetalleCompra.objects.create(
            compra=self.compra, producto=self.producto, cantidad=1,
            costo_unitario=Decimal('0.00'),
        ))

    # -- Lote ----------------------------------------------------------------

    def _lote(self, **campos):
        base = dict(
            producto=self.producto,
            numero_lote='DBC-L-1',
            fecha_compra=datetime(2026, 1, 1, tzinfo=dt_timezone.utc),
            cantidad_inicial=10,
            cantidad_actual=10,
            costo_unitario=Decimal('40.00'),
        )
        base.update(campos)
        return Lote.objects.create(**base)

    def test_lote_cantidad_inicial_cero_rechazada(self):
        self._rechaza(lambda: self._lote(numero_lote='DBC-L-BAD-1', cantidad_inicial=0))

    def test_lote_costo_cero_rechazado(self):
        self._rechaza(lambda: self._lote(numero_lote='DBC-L-BAD-2', costo_unitario=Decimal('0.00')))

    def test_lote_cantidad_actual_negativa_se_acepta(self):
        # Inventario negativo es politica valida: no se restringe cantidad_actual.
        lote = self._lote(numero_lote='DBC-L-NEG', cantidad_actual=-3)
        self.assertEqual(lote.cantidad_actual, -3)
