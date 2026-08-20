"""
apps/ventas/tests/test_anulaciones.py

Regresion de `anular_venta_service`, del plazo configurable de anulacion y de
la inmutabilidad de la venta desde Django Admin.

Hallazgos cubiertos (docs/exploracion/AUDITORIA_CODIGO_APPS_VENTAS.md):
VENTAS-004, VENTAS-008, VENTAS-009, VENTAS-013, VENTAS-014.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from apps.configuracion.models import ConfiguracionNegocio
from apps.inventario.models import Compra, DetalleCompra, Lote, MovimientoLote
from apps.permisos.testing import habilitar_cajero
from apps.productos.models import Categoria, Producto
from apps.ventas.admin import DetalleVentaInline, PagoInline, VentaAdmin
from apps.ventas.models import DetalleVenta, Pago, Venta
from apps.ventas.services import (
    AnulacionNoPermitidaError,
    VentaNoEncontradaError,
    anular_venta_service,
    procesar_venta_service,
)


class AnulacionTestCase(TestCase):
    def setUp(self):
        cache.clear()

        User = get_user_model()
        self.admin = User.objects.create_user(
            username='admin_anulaciones',
            email='admin_anulaciones@test.local',
            password='pass',
            rol='ADMIN',
            activo=True,
        )
        self.cajera = User.objects.create_user(
            username='cajera_anulaciones',
            email='cajera_anulaciones@test.local',
            password='pass',
            rol='CAJERA',
            activo=True,
        )
        habilitar_cajero(self.cajera)

        self.categoria = Categoria.objects.create(nombre='Anulaciones Test')
        self.producto = self._crear_producto('ANUL-SVC-001', Decimal('100.00'))
        self._ingresar_stock(self.producto, cantidad=10, costo=Decimal('40.00'))

    def tearDown(self):
        cache.clear()

    # -- helpers ------------------------------------------------------------

    def _crear_producto(self, sku, precio):
        return Producto.objects.create(
            sku=sku,
            codigo_barras=sku,
            nombre=f'Producto {sku}',
            descripcion='',
            categoria=self.categoria,
            precio_venta=precio,
            stock_minimo=1,
            activo=True,
            estado='nuevo',
            marca='',
            atributos={},
        )

    def _ingresar_stock(self, producto, *, cantidad, costo):
        compra = Compra.objects.create(
            usuario=self.admin,
            proveedor='Proveedor Test',
            numero_factura=f'FAC-ANUL-{producto.sku}',
            total=costo * cantidad,
        )
        return DetalleCompra.objects.create(
            compra=compra,
            producto=producto,
            cantidad=cantidad,
            costo_unitario=costo,
            subtotal=costo * cantidad,
        )

    def _vender(self, producto=None, cantidad=2, precio='100.00'):
        producto = producto or self.producto
        return procesar_venta_service(
            usuario=self.cajera,
            datos={
                'carrito': [{
                    'id': producto.id,
                    'cantidad': cantidad,
                    'precio_venta': precio,
                    'descuento': '0.00',
                }],
                'metodo_pago': 'efectivo',
                'total': str(Decimal(precio) * cantidad),
            },
        )

    def _set_config(self, **campos):
        config = ConfiguracionNegocio.load()
        for campo, valor in campos.items():
            setattr(config, campo, valor)
        config.save()
        cache.clear()
        return config


class AnulacionIdempotenteTests(AnulacionTestCase):
    """VENTAS-008: anular dos veces no puede devolver el stock dos veces."""

    def test_segunda_anulacion_es_rechazada_y_no_infla_el_stock(self):
        venta = self._vender()
        lote = Lote.objects.get(producto=self.producto)
        self.assertEqual(lote.cantidad_actual, 8)

        anular_venta_service(
            usuario=self.admin,
            venta_id=venta.id,
            motivo='Devolucion del cliente',
        )

        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 10)

        with self.assertRaises(AnulacionNoPermitidaError):
            anular_venta_service(
                usuario=self.admin,
                venta_id=venta.id,
                motivo='Devolucion del cliente',
            )

        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 10)

    def test_reversa_fifo_repetida_es_no_op(self):
        """
        Guard de más bajo nivel: aunque alguien llame dos veces a la reversa
        (dos requests que se cruzan antes de commitear), la segunda no vuelve
        a sumar unidades.
        """
        from apps.inventario.fifo_logic import anular_venta_devolver_stock

        venta = self._vender()
        lote = Lote.objects.get(producto=self.producto)

        primera = anular_venta_devolver_stock(venta_id=venta.id, usuario=self.admin)
        segunda = anular_venta_devolver_stock(venta_id=venta.id, usuario=self.admin)

        self.assertTrue(primera['success'])
        self.assertTrue(segunda['success'])
        self.assertTrue(segunda.get('ya_revertida'))

        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 10)
        self.assertEqual(
            MovimientoLote.objects.filter(
                tipo='ANULACION', referencia_id=venta.id
            ).count(),
            1,
        )


class AnulacionSinMovimientosTests(AnulacionTestCase):
    """VENTAS-009: una venta con inventario negativo debe poder anularse."""

    def test_venta_sin_lotes_se_puede_anular(self):
        self._set_config(permitir_inventario_negativo=True)
        producto_sin_stock = self._crear_producto('ANUL-NEG-001', Decimal('50.00'))

        venta = self._vender(producto=producto_sin_stock, cantidad=1, precio='50.00')
        self.assertEqual(
            MovimientoLote.objects.filter(tipo='VENTA', referencia_id=venta.id).count(),
            0,
        )

        anulada = anular_venta_service(
            usuario=self.admin,
            venta_id=venta.id,
            motivo='Venta cargada por error',
        )

        self.assertEqual(anulada.estado, 'ANULADA')
        self.assertIsNotNone(anulada.fecha_anulacion)


class ContratoHttpTests(AnulacionTestCase):
    """VENTAS-014: una venta inexistente es 404, no 500."""

    def test_venta_inexistente_levanta_error_404(self):
        with self.assertRaises(VentaNoEncontradaError) as ctx:
            anular_venta_service(
                usuario=self.admin,
                venta_id=999999,
                motivo='Motivo suficientemente largo',
            )

        self.assertEqual(ctx.exception.status_code, 404)

    def test_endpoint_responde_404_para_venta_inexistente(self):
        self.client.force_login(self.admin)

        respuesta = self.client.post(
            '/pos/api/anular-venta/',
            data={'venta_id': 999999, 'motivo': 'Motivo suficientemente largo'},
            content_type='application/json',
        )

        self.assertEqual(respuesta.status_code, 404)
        self.assertFalse(respuesta.json()['success'])


class PlazoDeAnulacionTests(AnulacionTestCase):
    """VENTAS-013: el plazo real es el configurado, y se compara con tz aware."""

    def test_plazo_usa_la_configuracion_del_negocio(self):
        self._set_config(dias_anulacion=5)

        venta = self._vender()
        venta.fecha_venta = timezone.now() - timedelta(days=7)
        venta.save(update_fields=['fecha_venta'])

        # Con el default fijo de 15 días esta venta seguía siendo anulable
        # aunque la configuración dijera 5.
        self.assertFalse(venta.puede_anularse())

        with self.assertRaises(AnulacionNoPermitidaError):
            anular_venta_service(
                usuario=self.admin,
                venta_id=venta.id,
                motivo='Fuera de plazo configurado',
            )

    def test_plazo_ampliado_habilita_la_anulacion(self):
        self._set_config(dias_anulacion=30)

        venta = self._vender()
        venta.fecha_venta = timezone.now() - timedelta(days=20)
        venta.save(update_fields=['fecha_venta'])

        self.assertTrue(venta.puede_anularse())

    def test_venta_recien_creada_es_anulable(self):
        venta = self._vender()
        self.assertTrue(venta.puede_anularse())

    def test_comparacion_de_plazo_no_depende_del_reloj_naive(self):
        """
        La venta vence exactamente dentro de N días. Con la comparación naive
        anterior, el resultado se corría tantas horas como offset tuviera el
        host (4 en un host UTC frente a Santo Domingo).
        """
        self._set_config(dias_anulacion=1)

        venta = self._vender()
        venta.fecha_venta = timezone.now() - timedelta(hours=23)
        venta.save(update_fields=['fecha_venta'])
        self.assertTrue(venta.puede_anularse())

        venta.fecha_venta = timezone.now() - timedelta(hours=25)
        venta.save(update_fields=['fecha_venta'])
        self.assertFalse(venta.puede_anularse())


class AdminInmutableTests(AnulacionTestCase):
    """VENTAS-004: el admin no puede mutar una venta cerrada."""

    def setUp(self):
        super().setUp()
        self.venta_admin = VentaAdmin(Venta, django_admin.site)

        User = get_user_model()
        self.staff = User.objects.create_superuser(
            username='staff_ventas_admin',
            email='staff_ventas_admin@test.local',
            password='pass',
        )
        self.staff.rol = 'ADMIN'
        self.staff.is_staff = True
        self.staff.activo = True
        self.staff.save(update_fields=['rol', 'is_staff', 'activo'])

    def test_no_se_pueden_crear_ni_borrar_ventas_desde_el_admin(self):
        self.assertFalse(self.venta_admin.has_add_permission(None))
        self.assertFalse(self.venta_admin.has_delete_permission(None))

    def test_todos_los_campos_de_la_venta_son_de_solo_lectura(self):
        venta = self._vender()
        editables = {
            campo.name
            for campo in Venta._meta.fields
            if campo.name not in ('id',)
        }
        readonly = set(self.venta_admin.readonly_fields)

        # `estado`, `usuario`, `notas` y `motivo_anulacion` eran editables:
        # cambiar `estado` a ANULADA no devolvía stock ni revertía CxC.
        self.assertEqual(editables - readonly, set())
        self.assertIn('estado', readonly)
        self.assertIn('motivo_anulacion', readonly)
        self.assertEqual(venta.estado, 'COMPLETADA')

    def test_los_inlines_de_detalle_y_pago_son_de_solo_lectura(self):
        for inline_cls, modelo in (
            (DetalleVentaInline, DetalleVenta),
            (PagoInline, Pago),
        ):
            inline = inline_cls(Venta, django_admin.site)
            with self.subTest(inline=inline_cls.__name__):
                self.assertFalse(inline.has_add_permission(None, None))
                self.assertFalse(inline.has_change_permission(None, None))
                self.assertFalse(inline.has_delete_permission(None, None))
                self.assertEqual(
                    set(inline.fields) - set(inline.readonly_fields), set()
                )

    def test_la_accion_de_anular_pasa_por_el_service(self):
        """
        La única mutación permitida desde el admin es la anulación, y debe
        producir los mismos efectos que el POS: stock devuelto y estado ANULADA.
        """
        venta = self._vender()
        lote = Lote.objects.get(producto=self.producto)
        self.assertEqual(lote.cantidad_actual, 8)

        self.client.force_login(self.staff)
        respuesta = self.client.post(
            '/admin/ventas/venta/',
            data={
                'action': 'anular_ventas',
                django_admin.helpers.ACTION_CHECKBOX_NAME: [str(venta.pk)],
                'aplicar': 'Confirmar anulación',
                'motivo': 'Anulacion desde el admin',
            },
            follow=True,
        )

        self.assertEqual(respuesta.status_code, 200)
        venta.refresh_from_db()
        lote.refresh_from_db()
        self.assertEqual(venta.estado, 'ANULADA')
        self.assertEqual(venta.motivo_anulacion, 'Anulacion desde el admin')
        self.assertEqual(lote.cantidad_actual, 10)
