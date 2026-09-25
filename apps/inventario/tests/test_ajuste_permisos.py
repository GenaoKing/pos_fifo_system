"""
INV-RBAC-SCOPE / CT-02 — consumidor de RBAC en el ajuste de inventario.

El ajuste se re-autoriza con `inventario.ajustar` contra la sucursal del LOTE
bloqueado, no solo en el decorador de la vista (que resuelve la sucursal del
OPERADOR). En una BD compartida por varias sucursales, un rol acotado a la
sucursal A no puede ajustar un lote de la B con solo cambiar el `lote_id`.

Matriz (espeja `apps/ventas/tests/test_anulacion_permisos.py`):
- rol custom positivo: `inventario.ajustar` en la sucursal del lote ajusta;
- permiso de otra sucursal negativo: el mismo rol acotado a otra sucursal no;
- sin permiso: denegado;
- acceso directo por URL respeta el mismo scope (`/inventario/api/ajustar/`);
- lote legacy sin sucursal cae al scope operativo del solicitante.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.inventario.models import Compra, DetalleCompra, Lote
from apps.inventario.services.ajustes_service import registrar_ajuste_service
from apps.inventario.services.exceptions import PermisoAjusteDenegadoError
from apps.negocios.models import Negocio
from apps.permisos.testing import asignar, crear_rol
from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal


class AjustePermisosBase(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()

        self.negocio = Negocio.objects.create(
            nombre='RBAC Ajustes', slug='rbac-ajustes',
        )
        self.suc_a = Sucursal.objects.create(
            negocio=self.negocio, codigo='SUC-A', nombre='Sucursal A',
        )
        self.suc_b = Sucursal.objects.create(
            negocio=self.negocio, codigo='SUC-B', nombre='Sucursal B',
        )

        # Quien registra las compras: acceso total, no es el sujeto de la prueba.
        self.admin = User.objects.create_user(
            username='admin_ajuste_perm', email='admin_ajuste_perm@t.local',
            password='x', rol='ADMIN', activo=True,
        )

        rol_ajustador = crear_rol(self.negocio, 'Ajustador', ['inventario.ajustar'])
        self.op_a = self._operador('op_ajuste_a')
        self.op_b = self._operador('op_ajuste_b')
        asignar(self.op_a, rol_ajustador, sucursal=self.suc_a)
        asignar(self.op_b, rol_ajustador, sucursal=self.suc_b)

        self.op_sin = self._operador('op_ajuste_sin')
        asignar(
            self.op_sin,
            crear_rol(self.negocio, 'Solo ver', ['inventario.ver']),
            sucursal=self.suc_a,
        )

        self.categoria = Categoria.objects.create(nombre='Ajuste Perm')
        self.producto = self._producto('AJU-PERM-1', Decimal('100.00'))

    def tearDown(self):
        cache.clear()

    # -- helpers -----------------------------------------------------------

    def _operador(self, username):
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

    def _lote_en(self, sucursal, cantidad=10, costo=Decimal('40.00')):
        compra = Compra.objects.create(
            usuario=self.admin, proveedor='Prov',
            numero_factura=f'F-{Compra.objects.count()}',
            total=costo * cantidad, sucursal=sucursal,
        )
        detalle = DetalleCompra.objects.create(
            compra=compra, producto=self.producto, cantidad=cantidad,
            costo_unitario=costo, subtotal=costo * cantidad,
        )
        return detalle.lote


class AjusteServicioScopeTests(AjustePermisosBase):
    def test_rol_custom_ajusta_en_la_sucursal_del_lote(self):
        lote = self._lote_en(self.suc_a)

        ajuste = registrar_ajuste_service(
            usuario=self.op_a, lote_id=lote.id, tipo='MERMA', cantidad=3,
            motivo='Merma por rotura en A',
        )

        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 7)
        self.assertEqual(ajuste.cantidad, -3)

    def test_permiso_de_otra_sucursal_no_ajusta(self):
        lote = self._lote_en(self.suc_a)

        with self.assertRaises(PermisoAjusteDenegadoError) as ctx:
            registrar_ajuste_service(
                usuario=self.op_b, lote_id=lote.id, tipo='MERMA', cantidad=3,
                motivo='Intento cross-branch desde B',
            )

        self.assertEqual(ctx.exception.status_code, 403)
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 10)

    def test_sin_permiso_ajustar_no_ajusta(self):
        lote = self._lote_en(self.suc_a)
        with self.assertRaises(PermisoAjusteDenegadoError):
            registrar_ajuste_service(
                usuario=self.op_sin, lote_id=lote.id, tipo='MERMA', cantidad=1,
                motivo='Operador sin permiso de ajustar',
            )
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 10)

    def test_lote_legacy_sin_sucursal_cae_al_scope_operador(self):
        lote = self._lote_en(self.suc_a)
        lote.sucursal = None
        lote.save(update_fields=['sucursal'])

        # Sin scope operador: se autoriza contra asignaciones globales -> deniega.
        with self.assertRaises(PermisoAjusteDenegadoError):
            registrar_ajuste_service(
                usuario=self.op_a, lote_id=lote.id, tipo='MERMA', cantidad=1,
                motivo='Legacy sin scope operador',
            )

        # Con el scope operativo de su instalacion (A) -> autoriza.
        ajuste = registrar_ajuste_service(
            usuario=self.op_a, lote_id=lote.id, tipo='MERMA', cantidad=1,
            motivo='Legacy con scope operador A', sucursal=self.suc_a,
        )
        self.assertEqual(ajuste.cantidad, -1)


class AjusteURLScopeTests(AjustePermisosBase):
    """Acceso directo por URL: el gate no depende de que la UI esconda el boton."""

    def _post(self, lote):
        return self.client.post(
            reverse('inventario:api_ajustar'),
            data={
                'lote_id': lote.id, 'tipo': 'MERMA', 'cantidad': 2,
                'motivo': 'Ajuste via URL de prueba',
            },
            content_type='application/json',
        )

    def test_url_rol_custom_de_su_sucursal_ajusta(self):
        lote = self._lote_en(self.suc_a)
        with self.settings(SUCURSAL_CODIGO='SUC-A'):
            cache.clear()
            self.client.force_login(self.op_a)
            resp = self._post(lote)

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 8)

    def test_url_acceso_directo_de_otra_sucursal_denega(self):
        lote = self._lote_en(self.suc_a)
        with self.settings(SUCURSAL_CODIGO='SUC-B'):
            cache.clear()
            self.client.force_login(self.op_b)
            resp = self._post(lote)

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()['success'])
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 10)
