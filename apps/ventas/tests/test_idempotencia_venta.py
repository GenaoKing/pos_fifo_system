"""
Idempotencia de la venta: dos peticiones con la misma `clave_idempotencia`
producen UNA sola venta (un solo efecto financiero: un cobro, un consumo de
inventario). Espeja la idempotencia de abonos CxC (PagoCxC.clave_idempotencia).

- reintento con la misma clave -> la venta ORIGINAL, stock consumido una vez;
- sin clave -> conducta anterior (cada llamada crea su venta);
- claves distintas -> dos ventas;
- la unica parcial de BD rechaza dos ventas con la misma clave (respaldo real).
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.inventario.models import Compra, DetalleCompra, Lote
from apps.permisos.testing import habilitar_cajero
from apps.productos.models import Categoria, Producto
from apps.ventas.models import Venta
from apps.ventas.services import procesar_venta_service


class VentaIdempotenciaTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.cajera = User.objects.create_user(
            username='cajera_idemp', email='cajera_idemp@test.local',
            password='x', rol='CAJERA', activo=True,
        )
        habilitar_cajero(self.cajera)

        self.categoria = Categoria.objects.create(nombre='Idempotencia')
        self.producto = Producto.objects.create(
            sku='IDEMP-1', codigo_barras='IDEMP-1', nombre='P', descripcion='',
            categoria=self.categoria, precio_venta=Decimal('100.00'),
            stock_minimo=1, activo=True, estado='nuevo', marca='', atributos={},
        )
        compra = Compra.objects.create(
            usuario=self.cajera, proveedor='Prov', numero_factura='F-IDEMP',
            total=Decimal('400.00'),
        )
        DetalleCompra.objects.create(
            compra=compra, producto=self.producto, cantidad=10,
            costo_unitario=Decimal('40.00'), subtotal=Decimal('400.00'),
        )

    def tearDown(self):
        cache.clear()

    def _vender(self, clave=None):
        datos = {
            'carrito': [{
                'id': self.producto.id, 'cantidad': 2,
                'precio_venta': '100.00', 'descuento': '0.00',
            }],
            'metodo_pago': 'efectivo', 'total': '200.00',
        }
        if clave is not None:
            datos['clave_idempotencia'] = clave
        return procesar_venta_service(usuario=self.cajera, datos=datos)

    def _stock(self):
        return Lote.objects.get(producto=self.producto).cantidad_actual

    def test_reintento_misma_clave_devuelve_la_venta_original(self):
        v1 = self._vender(clave='V-K1')
        self.assertEqual(self._stock(), 8)

        v2 = self._vender(clave='V-K1')

        self.assertEqual(v1.id, v2.id)
        self.assertEqual(Venta.objects.count(), 1)
        # Un solo efecto financiero: el stock se consumio UNA vez.
        self.assertEqual(self._stock(), 8)

    def test_sin_clave_conserva_conducta_anterior(self):
        self._vender()
        self._vender()
        self.assertEqual(Venta.objects.count(), 2)
        self.assertEqual(self._stock(), 6)

    def test_claves_distintas_crean_dos_ventas(self):
        self._vender(clave='A')
        self._vender(clave='B')
        self.assertEqual(Venta.objects.count(), 2)

    def test_constraint_db_rechaza_clave_duplicada(self):
        self._vender(clave='V-DUP')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Venta.objects.create(
                    numero_venta='V-IDEMP-DIRECT', usuario=self.cajera,
                    subtotal=Decimal('100.00'), total=Decimal('100.00'),
                    estado='COMPLETADA', clave_idempotencia='V-DUP',
                )

    def test_dos_ventas_sin_clave_conviven(self):
        # La unica es PARCIAL: null no colisiona con null.
        self._vender()
        self._vender()
        self.assertEqual(
            Venta.objects.filter(clave_idempotencia__isnull=True).count(), 2
        )
