"""
apps/ventas/tests/test_reimpresion_permisos.py

PER-013 / CT-02 — consumidor de RBAC en la reimpresión.

Reimprimir vuelve a emitir el documento de una venta ya registrada. Todas las
superficies exigen `ventas.reimprimir` y resuelven la venta dentro del alcance
operativo del usuario (no solo el permiso: también el scope):

- ticket térmico:   POST /impresion/reimprimir/<id>/   (JSON, `ventas.reimprimir`)
- lista reimprimir: GET  /impresion/reimprimir/        (HTML, `ventas.reimprimir`)
- comprobante PDF:  GET  /pos/venta/<id>/comprobante/  (HTML, `ventas.reimprimir`)

La instalación opera como SUC-A; una venta de SUC-B queda fuera del alcance.
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.configuracion.models import ConfiguracionNegocio
from apps.inventario.models import Compra, DetalleCompra
from apps.negocios.models import Negocio
from apps.permisos.testing import asignar, crear_rol
from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal
from apps.ventas.services import procesar_venta_service
from utils.impresoras.manager import print_manager


@override_settings(SUCURSAL_CODIGO='SUC-A')
class ReimpresionPermisosTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()

        self.negocio = Negocio.objects.create(
            nombre='RBAC Reimpresion', slug='rbac-reimpresion',
        )
        self.suc_a = Sucursal.objects.create(
            negocio=self.negocio, codigo='SUC-A', nombre='Sucursal A',
        )
        self.suc_b = Sucursal.objects.create(
            negocio=self.negocio, codigo='SUC-B', nombre='Sucursal B',
        )
        ConfiguracionNegocio.objects.create(sucursal=self.suc_a)

        self.admin = User.objects.create_user(
            username='admin_reimp', email='admin_reimp@t.local', password='x',
            rol='ADMIN', activo=True,
        )

        rol_reimp = crear_rol(self.negocio, 'Reimpresor', ['ventas.reimprimir'])
        self.reimpresor_a = self._cajero('reimpresor_a')
        self.reimpresor_b = self._cajero('reimpresor_b')
        asignar(self.reimpresor_a, rol_reimp, sucursal=self.suc_a)
        asignar(self.reimpresor_b, rol_reimp, sucursal=self.suc_b)

        self.sin_reimprimir = self._cajero('sin_reimprimir')
        asignar(
            self.sin_reimprimir,
            crear_rol(self.negocio, 'Solo ventas reimp', ['ventas.crear']),
            sucursal=self.suc_a,
        )

        self.categoria = Categoria.objects.create(nombre='Reimp')
        self.producto = self._producto('REIMP-1', Decimal('100.00'))
        self._stock(self.producto, 20, Decimal('40.00'))

        cache.clear()
        # La instalación (SUC-A) numera sus ventas en SUC-A; una de ellas se
        # reasigna a SUC-B para representar la venta de otra sucursal.
        self.venta_a = self._venta()
        self.venta_b = self._venta()
        self.venta_b.sucursal = self.suc_b
        self.venta_b.save(update_fields=['sucursal'])

    def tearDown(self):
        cache.clear()

    # -- helpers -----------------------------------------------------------

    def _cajero(self, username):
        return get_user_model().objects.create_user(
            username=username, email=f'{username}@t.local', password='x',
            rol='CAJERA', activo=True,
        )

    def _producto(self, sku, precio):
        return Producto.objects.create(
            sku=sku, codigo_barras=sku, nombre=f'Producto {sku}', descripcion='',
            categoria=self.categoria, precio_venta=precio, stock_minimo=1,
            activo=True, estado='nuevo', marca='', atributos={},
        )

    def _stock(self, producto, cantidad, costo):
        compra = Compra.objects.create(
            usuario=self.admin, proveedor='Prov',
            numero_factura=f'F-{producto.sku}', total=costo * cantidad,
        )
        DetalleCompra.objects.create(
            compra=compra, producto=producto, cantidad=cantidad,
            costo_unitario=costo, subtotal=costo * cantidad,
        )

    def _venta(self):
        return procesar_venta_service(
            usuario=self.admin,
            datos={
                'carrito': [{
                    'id': self.producto.id, 'cantidad': 1,
                    'precio_venta': '100.00', 'descuento': '0.00',
                }],
                'metodo_pago': 'efectivo', 'total': '100.00',
            },
        )

    # -- ticket térmico ----------------------------------------------------

    def test_reimprimir_ticket_sin_permiso_403(self):
        self.client.force_login(self.sin_reimprimir)
        resp = self.client.post(
            reverse('impresion:reimprimir_ticket', args=[self.venta_a.id])
        )
        self.assertEqual(resp.status_code, 403)

    def test_reimprimir_ticket_permiso_de_otra_sucursal_403(self):
        # reimpresor_b tiene el permiso, pero en SUC-B; la instalación es SUC-A.
        self.client.force_login(self.reimpresor_b)
        resp = self.client.post(
            reverse('impresion:reimprimir_ticket', args=[self.venta_a.id])
        )
        self.assertEqual(resp.status_code, 403)

    def test_reimprimir_ticket_cross_branch_404(self):
        # Con permiso en SUC-A, pero la venta es de SUC-B: fuera de alcance -> 404.
        self.client.force_login(self.reimpresor_a)
        resp = self.client.post(
            reverse('impresion:reimprimir_ticket', args=[self.venta_b.id])
        )
        self.assertEqual(resp.status_code, 404)

    def test_reimprimir_ticket_rol_custom_de_su_sucursal_200(self):
        self.client.force_login(self.reimpresor_a)
        with patch.object(
            print_manager, 'print_ticket_venta',
            return_value={'success': True, 'mensaje': 'ok'},
        ) as mock_print:
            resp = self.client.post(
                reverse('impresion:reimprimir_ticket', args=[self.venta_a.id])
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
        mock_print.assert_called_once()

    # -- lista de reimpresión ---------------------------------------------

    def test_lista_reimprimir_sin_permiso_redirect(self):
        self.client.force_login(self.sin_reimprimir)
        resp = self.client.get(reverse('impresion:lista_reimprimir'))
        self.assertEqual(resp.status_code, 302)

    def test_lista_reimprimir_scope_solo_su_sucursal(self):
        self.client.force_login(self.reimpresor_a)
        resp = self.client.get(reverse('impresion:lista_reimprimir'))
        self.assertEqual(resp.status_code, 200)
        ids = {v.id for v in resp.context['ventas']}
        self.assertIn(self.venta_a.id, ids)
        self.assertNotIn(self.venta_b.id, ids)

    # -- comprobante PDF (ya scoped; regresión) ----------------------------

    def test_comprobante_pdf_sin_permiso_redirect(self):
        self.client.force_login(self.sin_reimprimir)
        resp = self.client.get(reverse('pos:comprobante_pdf', args=[self.venta_a.id]))
        self.assertEqual(resp.status_code, 302)

    def test_comprobante_pdf_cross_branch_404(self):
        self.client.force_login(self.reimpresor_a)
        resp = self.client.get(reverse('pos:comprobante_pdf', args=[self.venta_b.id]))
        self.assertEqual(resp.status_code, 404)
