"""
Tests de la edición de compras (apps.inventario.views.compra_editar).

Foco: integridad FIFO. Una línea de compra solo puede editarse mientras su
lote esté intacto; si ya hubo consumo/ajuste la línea queda bloqueada.
"""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.productos.models import Categoria, Producto
from apps.inventario.models import Compra, DetalleCompra, Lote, MovimientoLote


class CompraEditarTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='admin_compras', email='admin_compras@example.com',
            password='pass', rol='ADMIN', activo=True,
        )
        self.cajera = User.objects.create_user(
            username='cajera_compras', email='cajera_compras@example.com',
            password='pass', rol='CAJERA', activo=True,
        )
        self.categoria = Categoria.objects.create(nombre='General')
        self.prod_a = self._producto('PROD-A', 'Producto A')
        self.prod_b = self._producto('PROD-B', 'Producto B')

        self.compra = Compra.objects.create(
            usuario=self.admin, proveedor='Proveedor Original',
            numero_factura='FAC-001', total=Decimal('0'),
        )
        # DetalleCompra.save() auto-genera Lote + MovimientoLote(COMPRA)
        self.detalle = DetalleCompra.objects.create(
            compra=self.compra, producto=self.prod_a,
            cantidad=10, costo_unitario=Decimal('5.00'), subtotal=Decimal('50.00'),
        )
        self.compra.total = Decimal('50.00')
        self.compra.save()
        self.lote = self.detalle.lote

    def _producto(self, sku, nombre):
        return Producto.objects.create(
            sku=sku, codigo_barras=sku, nombre=nombre, descripcion='',
            categoria=self.categoria, precio_venta='100.00', stock_minimo=1,
            activo=True, estado='nuevo', marca='', atributos={},
        )

    def _post(self, payload):
        url = reverse('inventario:compra_editar', args=[self.compra.id])
        return self.client.post(url, data=json.dumps(payload),
                                content_type='application/json')

    def _linea(self, **over):
        base = {
            'detalle_id': self.detalle.id,
            'producto_id': self.detalle.producto_id,
            'cantidad': self.detalle.cantidad,
            'costo_unitario': float(self.detalle.costo_unitario),
            'eliminar': False,
        }
        base.update(over)
        return base

    # ----- línea intacta: edición exitosa -----

    def test_edita_cantidad_y_costo_de_linea_intacta(self):
        self.client.force_login(self.admin)
        resp = self._post({
            'proveedor': 'Proveedor Corregido',
            'numero_factura': 'FAC-001',
            'notas': 'corrección de precio',
            'lineas': [self._linea(cantidad=8, costo_unitario=7.50)],
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])

        self.detalle.refresh_from_db()
        self.lote.refresh_from_db()
        self.compra.refresh_from_db()

        self.assertEqual(self.detalle.cantidad, 8)
        self.assertEqual(self.detalle.costo_unitario, Decimal('7.50'))
        self.assertEqual(self.detalle.subtotal, Decimal('60.00'))
        # El lote intacto sigue el cambio (inicial == actual)
        self.assertEqual(self.lote.cantidad_inicial, 8)
        self.assertEqual(self.lote.cantidad_actual, 8)
        self.assertEqual(self.lote.costo_unitario, Decimal('7.50'))
        # El movimiento COMPRA se actualiza
        mov = self.lote.movimientos.get(tipo='COMPRA')
        self.assertEqual(mov.cantidad, 8)
        self.assertEqual(mov.cantidad_nueva, 8)
        # Total recalculado y cabecera actualizada
        self.assertEqual(self.compra.total, Decimal('60.00'))
        self.assertEqual(self.compra.proveedor, 'Proveedor Corregido')
        # No se creó un lote duplicado
        self.assertEqual(Lote.objects.filter(detalle_compra=self.detalle).count(), 1)

    def test_cambia_producto_de_linea_intacta(self):
        self.client.force_login(self.admin)
        resp = self._post({
            'proveedor': 'Proveedor Original',
            'numero_factura': 'FAC-001',
            'notas': '',
            'lineas': [self._linea(producto_id=self.prod_b.id)],
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        self.detalle.refresh_from_db()
        self.lote.refresh_from_db()
        self.assertEqual(self.detalle.producto_id, self.prod_b.id)
        self.assertEqual(self.lote.producto_id, self.prod_b.id)

    # ----- línea consumida: bloqueada -----

    def test_linea_consumida_no_se_puede_editar(self):
        self.client.force_login(self.admin)
        # Simula una venta: consume 4 unidades del lote
        self.lote.cantidad_actual = 6
        self.lote.save()
        MovimientoLote.objects.create(
            lote=self.lote, tipo='VENTA', cantidad=-4,
            cantidad_anterior=10, cantidad_nueva=6,
            referencia_tipo='Venta', referencia_id=999, usuario=self.admin,
        )

        resp = self._post({
            'proveedor': 'Proveedor Original',
            'numero_factura': 'FAC-001',
            'notas': '',
            'lineas': [self._linea(cantidad=3, costo_unitario=99.0)],
        })
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['success'])

        # Nada cambió en la línea bloqueada
        self.detalle.refresh_from_db()
        self.lote.refresh_from_db()
        self.assertEqual(self.detalle.cantidad, 10)
        self.assertEqual(self.detalle.costo_unitario, Decimal('5.00'))
        self.assertEqual(self.lote.cantidad_actual, 6)

    def test_linea_consumida_permite_corregir_solo_el_costo(self):
        """El costo es editable aunque el lote tenga movimiento: no toca
        cantidades FIFO y las ventas pasadas conservan su costo_fifo."""
        self.client.force_login(self.admin)
        self.lote.cantidad_actual = 6
        self.lote.save()
        MovimientoLote.objects.create(
            lote=self.lote, tipo='VENTA', cantidad=-4,
            cantidad_anterior=10, cantidad_nueva=6,
            referencia_tipo='Venta', referencia_id=999, usuario=self.admin,
        )

        resp = self._post({
            'proveedor': 'Proveedor Original',
            'numero_factura': 'FAC-001',
            'notas': 'corrección de costo',
            'lineas': [self._linea(costo_unitario=7.25)],  # solo cambia el costo
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['success'])

        self.detalle.refresh_from_db()
        self.lote.refresh_from_db()
        self.compra.refresh_from_db()

        # Costo propagado a detalle + lote, subtotal y total recalculados
        self.assertEqual(self.detalle.costo_unitario, Decimal('7.25'))
        self.assertEqual(self.detalle.subtotal, Decimal('72.50'))
        self.assertEqual(self.lote.costo_unitario, Decimal('7.25'))
        self.assertEqual(self.compra.total, Decimal('72.50'))
        # Las cantidades FIFO no se tocan
        self.assertEqual(self.detalle.cantidad, 10)
        self.assertEqual(self.lote.cantidad_inicial, 10)
        self.assertEqual(self.lote.cantidad_actual, 6)
        mov = self.lote.movimientos.get(tipo='COMPRA')
        self.assertEqual(mov.cantidad, 10)
        self.assertEqual(mov.cantidad_nueva, 10)
        # No se creó un lote duplicado
        self.assertEqual(Lote.objects.filter(detalle_compra=self.detalle).count(), 1)

    def test_linea_consumida_costo_invalido_rechazado(self):
        self.client.force_login(self.admin)
        self.lote.cantidad_actual = 6
        self.lote.save()
        MovimientoLote.objects.create(
            lote=self.lote, tipo='VENTA', cantidad=-4,
            cantidad_anterior=10, cantidad_nueva=6,
            referencia_tipo='Venta', referencia_id=999, usuario=self.admin,
        )
        resp = self._post({
            'proveedor': 'Proveedor Original',
            'numero_factura': 'FAC-001',
            'notas': '',
            'lineas': [self._linea(costo_unitario=-1)],
        })
        self.assertEqual(resp.status_code, 400)
        self.detalle.refresh_from_db()
        self.assertEqual(self.detalle.costo_unitario, Decimal('5.00'))

    def test_cabecera_editable_aunque_linea_este_consumida(self):
        """La cabecera se puede corregir si las líneas consumidas no cambian."""
        self.client.force_login(self.admin)
        self.lote.cantidad_actual = 6
        self.lote.save()
        MovimientoLote.objects.create(
            lote=self.lote, tipo='VENTA', cantidad=-4,
            cantidad_anterior=10, cantidad_nueva=6,
            referencia_tipo='Venta', referencia_id=999, usuario=self.admin,
        )
        resp = self._post({
            'proveedor': 'Nuevo Proveedor',
            'numero_factura': 'FAC-XYZ',
            'notas': 'solo cabecera',
            'lineas': [self._linea()],  # sin cambios en la línea
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        self.compra.refresh_from_db()
        self.assertEqual(self.compra.proveedor, 'Nuevo Proveedor')
        self.assertEqual(self.compra.numero_factura, 'FAC-XYZ')

    # ----- agregar / eliminar líneas -----

    def test_agregar_nueva_linea_genera_lote(self):
        self.client.force_login(self.admin)
        resp = self._post({
            'proveedor': 'Proveedor Original',
            'numero_factura': 'FAC-001',
            'notas': '',
            'lineas': [
                self._linea(),
                {'detalle_id': None, 'producto_id': self.prod_b.id,
                 'cantidad': 5, 'costo_unitario': 2.00, 'eliminar': False},
            ],
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        self.compra.refresh_from_db()
        self.assertEqual(self.compra.detalles.count(), 2)
        nuevo = self.compra.detalles.get(producto=self.prod_b)
        self.assertTrue(Lote.objects.filter(detalle_compra=nuevo).exists())
        self.assertEqual(self.compra.total, Decimal('60.00'))  # 50 + 10

    def test_eliminar_linea_intacta_anula_el_lote_sin_borrar_su_historial(self):
        self.client.force_login(self.admin)
        # Agrega una segunda línea para poder borrar la primera
        d2 = DetalleCompra.objects.create(
            compra=self.compra, producto=self.prod_b,
            cantidad=5, costo_unitario=Decimal('2.00'), subtotal=Decimal('10.00'),
        )
        lote2_id = d2.lote.id
        resp = self._post({
            'proveedor': 'Proveedor Original',
            'numero_factura': 'FAC-001',
            'notas': '',
            'lineas': [
                self._linea(eliminar=True),
                {'detalle_id': d2.id, 'producto_id': self.prod_b.id,
                 'cantidad': 5, 'costo_unitario': 2.00, 'eliminar': False},
            ],
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(DetalleCompra.objects.filter(id=self.detalle.id).exists())
        self.assertTrue(Lote.objects.filter(id=lote2_id).exists())
        self.compra.refresh_from_db()
        self.assertEqual(self.compra.total, Decimal('10.00'))

        # INVENTARIO-010: el lote NO se borra. Borrarlo se llevaba por CASCADE
        # sus MovimientoLote, y el ledger local perdia una entrada que ya pudo
        # haberse sincronizado o conciliado. Ahora queda anulado, en cero y
        # desvinculado de la compra, con su historial intacto.
        self.lote.refresh_from_db()
        self.assertFalse(self.lote.activo)
        self.assertEqual(self.lote.cantidad_actual, 0)
        self.assertIsNone(self.lote.detalle_compra_id)

        movimientos = list(
            self.lote.movimientos.order_by('id').values_list('tipo', 'cantidad')
        )
        # Entrada de compra + contrapartida: la secuencia cuadra en cero.
        self.assertEqual(movimientos, [('COMPRA', 10), ('AJUSTE', -10)])
        self.assertEqual(sum(c for _, c in movimientos), 0)

    # ----- render del formulario -----

    def test_get_renderiza_formulario(self):
        self.client.force_login(self.admin)
        url = reverse('inventario:compra_editar', args=[self.compra.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'compra-init-data')
        self.assertContains(resp, self.compra.numero_compra)

    # ----- permisos -----

    def test_cajera_no_puede_editar(self):
        self.client.force_login(self.cajera)
        resp = self._post({
            'proveedor': 'X', 'numero_factura': '', 'notas': '',
            'lineas': [self._linea()],
        })
        # La vista redirige a la lista (no es admin)
        self.assertEqual(resp.status_code, 302)
        self.detalle.refresh_from_db()
        self.assertEqual(self.detalle.cantidad, 10)
