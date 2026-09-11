"""
apps/ventas/tests/test_anulacion_permisos.py

PER-013 / CT-02 — consumidor de RBAC en la anulación de ventas.

La anulación se re-autoriza con `ventas.anular` contra la sucursal de la PROPIA
venta bloqueada, no contra el rol legacy ni un scope del cliente. Matriz:

- rol custom positivo: permiso `ventas.anular` en la sucursal de la venta anula;
- permiso de otra sucursal negativo: el mismo rol acotado a otra sucursal no;
- acceso directo por URL respeta el mismo scope (`/pos/api/anular-venta/`);
- no exposición cross-branch: el listado de anulaciones solo muestra las ventas
  del alcance operativo;
- venta legacy sin sucursal cae al scope operativo del solicitante.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.configuracion.models import ConfiguracionNegocio
from apps.inventario.models import Compra, DetalleCompra, Lote
from apps.negocios.models import Negocio
from apps.permisos.testing import asignar, crear_rol
from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal
from apps.ventas.services import (
    PermisoDenegadoError,
    anular_venta_service,
    procesar_venta_service,
)


class AnulacionPermisosBase(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()

        self.negocio = Negocio.objects.create(
            nombre='RBAC Anulaciones', slug='rbac-anulaciones',
        )
        self.suc_a = Sucursal.objects.create(
            negocio=self.negocio, codigo='SUC-A', nombre='Sucursal A',
        )
        self.suc_b = Sucursal.objects.create(
            negocio=self.negocio, codigo='SUC-B', nombre='Sucursal B',
        )
        # Config propia de la sucursal operativa, creada por la secuencia (no por
        # el atajo pk=1 de `load()` sin sucursal, que envenena la secuencia y hace
        # chocar el siguiente INSERT). Con una sola config, `get_config()` resuelve
        # sin ambigüedad tanto bajo el código por defecto como bajo SUC-A.
        ConfiguracionNegocio.load(sucursal=self.suc_a)

        # Quien crea las ventas: acceso total, no es el sujeto de la prueba.
        self.admin = User.objects.create_user(
            username='admin_anul_perm', email='admin_anul_perm@t.local',
            password='x', rol='ADMIN', activo=True,
        )

        # Rol custom con SOLO ventas.anular, asignado por sucursal a cada cajero.
        rol_anulador = crear_rol(self.negocio, 'Anulador', ['ventas.anular'])
        self.cajero_a = self._cajero('cajero_anul_a')
        self.cajero_b = self._cajero('cajero_anul_b')
        asignar(self.cajero_a, rol_anulador, sucursal=self.suc_a)
        asignar(self.cajero_b, rol_anulador, sucursal=self.suc_b)

        # Un cajero SIN ventas.anular (solo puede vender).
        self.cajero_sin = self._cajero('cajero_sin_anul')
        asignar(
            self.cajero_sin,
            crear_rol(self.negocio, 'Solo ventas', ['ventas.crear']),
            sucursal=self.suc_a,
        )

        self.categoria = Categoria.objects.create(nombre='Anul Perm')
        self.producto = self._producto('ANUL-PERM-1', Decimal('100.00'))
        self._stock(self.producto, 10, Decimal('40.00'))

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

    def _venta_en(self, sucursal):
        venta = procesar_venta_service(
            usuario=self.admin,
            datos={
                'carrito': [{
                    'id': self.producto.id, 'cantidad': 2,
                    'precio_venta': '100.00', 'descuento': '0.00',
                }],
                'metodo_pago': 'efectivo', 'total': '200.00',
            },
        )
        venta.sucursal = sucursal
        venta.save(update_fields=['sucursal'])
        return venta


class AnulacionServicioScopeTests(AnulacionPermisosBase):
    def test_rol_custom_anula_en_la_sucursal_de_la_venta(self):
        venta = self._venta_en(self.suc_a)
        self.assertEqual(Lote.objects.get(producto=self.producto).cantidad_actual, 8)

        anulada = anular_venta_service(
            usuario=self.cajero_a, venta_id=venta.id,
            motivo='Devolucion del cliente en A',
        )

        self.assertEqual(anulada.estado, 'ANULADA')
        self.assertEqual(Lote.objects.get(producto=self.producto).cantidad_actual, 10)

    def test_permiso_de_otra_sucursal_no_anula(self):
        venta = self._venta_en(self.suc_a)

        with self.assertRaises(PermisoDenegadoError) as ctx:
            anular_venta_service(
                usuario=self.cajero_b, venta_id=venta.id,
                motivo='Intento cross-branch desde B',
            )

        self.assertEqual(ctx.exception.status_code, 403)
        venta.refresh_from_db()
        self.assertEqual(venta.estado, 'COMPLETADA')
        # El stock no se devolvió: la anulación nunca ocurrió.
        self.assertEqual(Lote.objects.get(producto=self.producto).cantidad_actual, 8)

    def test_sin_permiso_anular_no_anula(self):
        venta = self._venta_en(self.suc_a)
        with self.assertRaises(PermisoDenegadoError):
            anular_venta_service(
                usuario=self.cajero_sin, venta_id=venta.id,
                motivo='Cajero sin permiso de anular',
            )

    def test_venta_legacy_sin_sucursal_cae_al_scope_operador(self):
        venta = self._venta_en(self.suc_a)
        venta.sucursal = None
        venta.save(update_fields=['sucursal'])

        # Sin scope operador: se autoriza contra asignaciones globales, y el rol
        # de A no es global -> deniega.
        with self.assertRaises(PermisoDenegadoError):
            anular_venta_service(
                usuario=self.cajero_a, venta_id=venta.id,
                motivo='Legacy sin scope operador',
            )
        venta.refresh_from_db()
        self.assertEqual(venta.estado, 'COMPLETADA')

        # Con el scope operativo de su instalación (A), la venta legacy queda
        # dentro de su alcance -> autoriza.
        anulada = anular_venta_service(
            usuario=self.cajero_a, venta_id=venta.id,
            motivo='Legacy con scope operador A', sucursal=self.suc_a,
        )
        self.assertEqual(anulada.estado, 'ANULADA')


class AnulacionURLScopeTests(AnulacionPermisosBase):
    """Acceso directo por URL: el gate no depende de que la UI esconda el botón."""

    def test_url_rol_custom_de_su_sucursal_anula(self):
        venta = self._venta_en(self.suc_a)
        self.client.force_login(self.cajero_a)

        resp = self.client.post(
            reverse('pos:api_anular_venta'),
            data={'venta_id': venta.id, 'motivo': 'Anulacion via URL en A'},
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
        venta.refresh_from_db()
        self.assertEqual(venta.estado, 'ANULADA')

    def test_url_acceso_directo_de_otra_sucursal_denega(self):
        venta = self._venta_en(self.suc_a)
        self.client.force_login(self.cajero_b)

        resp = self.client.post(
            reverse('pos:api_anular_venta'),
            data={'venta_id': venta.id, 'motivo': 'Intento directo cross-branch'},
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()['success'])
        venta.refresh_from_db()
        self.assertEqual(venta.estado, 'COMPLETADA')


class VistaAnulacionesScopeTests(AnulacionPermisosBase):
    """`vista_anulaciones`: gate y listado acotados a la sucursal operativa."""

    def test_muestra_solo_ventas_de_su_sucursal(self):
        venta_a = self._venta_en(self.suc_a)
        venta_b = self._venta_en(self.suc_b)

        with self.settings(SUCURSAL_CODIGO='SUC-A'):
            cache.clear()
            self.client.force_login(self.cajero_a)
            resp = self.client.get(reverse('pos:anulaciones'))

        self.assertEqual(resp.status_code, 200)
        ids = {v['id'] for v in resp.context['init_data_json']['ventas']}
        self.assertIn(venta_a.id, ids)
        self.assertNotIn(venta_b.id, ids)

    def test_rol_de_otra_sucursal_no_entra(self):
        with self.settings(SUCURSAL_CODIGO='SUC-A'):
            cache.clear()
            self.client.force_login(self.cajero_b)
            resp = self.client.get(reverse('pos:anulaciones'))

        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('pos:punto_venta'), resp.url)
