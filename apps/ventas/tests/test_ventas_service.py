"""
apps/ventas/tests/test_ventas_service.py

Regresion de las invariantes de `procesar_venta_service`.

Cada test referencia el hallazgo de `docs/exploracion/AUDITORIA_CODIGO_APPS_VENTAS.md`
que lo motiva. El criterio es siempre el mismo: un payload que el navegador no
produce (o una carrera que la UI no puede evitar) no debe poder crear una venta
que descuadre inventario, caja o el documento fiscal.
"""
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.clientes.models import Cliente
from apps.configuracion.models import ConfiguracionNegocio
from apps.cotizaciones.models import Cotizacion, DetalleCotizacion
from apps.inventario.models import Compra, DetalleCompra, Lote, MovimientoLote
from apps.permisos.testing import habilitar_cajero
from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal
from apps.ventas.models import Venta
from apps.ventas.services import (
    CotizacionInvalidaError,
    ItemCarritoInvalidoError,
    MetodoPagoInvalidoError,
    PermisoDenegadoError,
    PrecioNoAutorizadoError,
    ProductoInexistenteError,
    StockInsuficienteError,
    SucursalNoResueltaError,
    TipoECFInvalidoError,
    TotalInconsistenteError,
    procesar_venta_service,
)


class VentaServiceTestCase(TestCase):
    """Fixture comun: un cajero con permiso de venta y un producto con stock."""

    def setUp(self):
        # get_config, get_sucursal_actual y el motor de permisos cachean en el
        # cache del proceso, que sobrevive entre tests. Sin limpiar, un test
        # arrastra la config (o peor, una Sucursal ya borrada) del anterior.
        cache.clear()

        User = get_user_model()
        self.admin = User.objects.create_user(
            username='admin_ventas_svc',
            email='admin_ventas_svc@test.local',
            password='pass',
            rol='ADMIN',
            activo=True,
        )
        self.cajera = User.objects.create_user(
            username='cajera_ventas_svc',
            email='cajera_ventas_svc@test.local',
            password='pass',
            rol='CAJERA',
            activo=True,
        )
        habilitar_cajero(self.cajera)

        self.categoria = Categoria.objects.create(nombre='Ventas Service Test')
        self.producto = self._crear_producto('VSVC-001', Decimal('100.00'))
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

    def _ingresar_stock(self, producto, *, cantidad, costo, factura=None):
        """Un DetalleCompra = un Lote (ver apps/inventario/models.py)."""
        compra = Compra.objects.create(
            usuario=self.admin,
            proveedor='Proveedor Test',
            numero_factura=factura or f'FAC-{producto.sku}-{cantidad}',
            total=costo * cantidad,
        )
        return DetalleCompra.objects.create(
            compra=compra,
            producto=producto,
            cantidad=cantidad,
            costo_unitario=costo,
            subtotal=costo * cantidad,
        )

    def _payload(self, **overrides):
        datos = {
            'carrito': [{
                'id': self.producto.id,
                'cantidad': 2,
                'precio_venta': '100.00',
                'descuento': '0.00',
            }],
            'metodo_pago': 'efectivo',
            'total': '200.00',
        }
        datos.update(overrides)
        return datos

    def _vender(self, usuario=None, **overrides):
        return procesar_venta_service(
            usuario=usuario or self.cajera,
            datos=self._payload(**overrides),
        )

    def _set_config(self, **campos):
        config = ConfiguracionNegocio.load()
        for campo, valor in campos.items():
            setattr(config, campo, valor)
        config.save()
        cache.clear()
        return config


class ValidacionDePayloadTests(VentaServiceTestCase):
    """VENTAS-006: el payload no puede traer importes imposibles."""

    def test_cantidad_cero_es_rechazada(self):
        with self.assertRaises(ItemCarritoInvalidoError):
            self._vender(
                carrito=[{'id': self.producto.id, 'cantidad': 0,
                          'precio_venta': '100.00'}],
                total='0.00',
            )
        self.assertFalse(Venta.objects.exists())

    def test_cantidad_negativa_es_rechazada(self):
        with self.assertRaises(ItemCarritoInvalidoError):
            self._vender(
                carrito=[{'id': self.producto.id, 'cantidad': -2,
                          'precio_venta': '100.00'}],
                total='-200.00',
            )
        self.assertFalse(Venta.objects.exists())

    def test_precio_no_positivo_es_rechazado(self):
        with self.assertRaises(ItemCarritoInvalidoError):
            self._vender(
                carrito=[{'id': self.producto.id, 'cantidad': 1,
                          'precio_venta': '0.00'}],
                total='0.00',
            )
        self.assertFalse(Venta.objects.exists())

    def test_descuento_mayor_al_subtotal_es_rechazado(self):
        with self.assertRaises(ItemCarritoInvalidoError):
            self._vender(
                carrito=[{'id': self.producto.id, 'cantidad': 1,
                          'precio_venta': '100.00', 'descuento': '150.00'}],
                total='-50.00',
            )
        self.assertFalse(Venta.objects.exists())

    def test_total_no_positivo_es_rechazado(self):
        with self.assertRaises(TotalInconsistenteError):
            self._vender(
                carrito=[{'id': self.producto.id, 'cantidad': 1,
                          'precio_venta': '100.00', 'descuento': '100.00'}],
                total='0.00',
            )
        self.assertFalse(Venta.objects.exists())

    def test_item_sin_id_es_rechazado(self):
        with self.assertRaises(ItemCarritoInvalidoError):
            self._vender(
                carrito=[{'cantidad': 1, 'precio_venta': '100.00'}],
                total='100.00',
            )

    def test_producto_inexistente_da_404(self):
        with self.assertRaises(ProductoInexistenteError) as ctx:
            self._vender(
                carrito=[{'id': 999999, 'cantidad': 1, 'precio_venta': '100.00'}],
                total='100.00',
            )
        self.assertEqual(ctx.exception.status_code, 404)


class AutorizacionYPrecioTests(VentaServiceTestCase):
    """VENTAS-005: RBAC server-side y precio resuelto por el servidor."""

    def test_usuario_sin_permiso_de_venta_no_puede_vender(self):
        User = get_user_model()
        sin_rol = User.objects.create_user(
            username='sin_rol_ventas',
            email='sin_rol_ventas@test.local',
            password='pass',
            rol='CAJERA',
            activo=True,
        )

        with self.assertRaises(PermisoDenegadoError) as ctx:
            self._vender(usuario=sin_rol)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertFalse(Venta.objects.exists())

    def test_descuento_requiere_permiso_de_descuento(self):
        User = get_user_model()
        cajera_sin_descuento = User.objects.create_user(
            username='cajera_sin_desc',
            email='cajera_sin_desc@test.local',
            password='pass',
            rol='CAJERA',
            activo=True,
        )
        habilitar_cajero(cajera_sin_descuento, permisos=['ventas.crear'])

        # Sin descuento sí puede vender...
        self._vender(usuario=cajera_sin_descuento)

        # ...pero aplicar uno requiere el permiso específico.
        with self.assertRaises(PermisoDenegadoError):
            procesar_venta_service(
                usuario=cajera_sin_descuento,
                datos=self._payload(
                    carrito=[{'id': self.producto.id, 'cantidad': 1,
                              'precio_venta': '100.00', 'descuento': '10.00'}],
                    total='90.00',
                ),
            )
        self.assertEqual(Venta.objects.count(), 1)

    def test_precio_arbitrario_del_cliente_es_rechazado(self):
        with self.assertRaises(PrecioNoAutorizadoError):
            self._vender(
                carrito=[{'id': self.producto.id, 'cantidad': 1,
                          'precio_venta': '1.00'}],
                total='1.00',
            )
        self.assertFalse(Venta.objects.exists())

    def test_precio_de_cotizacion_si_esta_autorizado(self):
        """Un precio histórico de cotización es una fuente legítima."""
        cotizacion = Cotizacion.objects.create(
            cliente=Cliente.get_cliente_contado(),
            usuario=self.cajera,
            total=Decimal('80.00'),
        )
        DetalleCotizacion.objects.create(
            cotizacion=cotizacion,
            producto=self.producto,
            cantidad=1,
            precio_unitario=Decimal('80.00'),
            descuento_monto=Decimal('0.00'),
            subtotal=Decimal('0.00'),
            total_linea=Decimal('0.00'),
        )

        venta = self._vender(
            carrito=[{'id': self.producto.id, 'cantidad': 1,
                      'precio_venta': '80.00'}],
            total='80.00',
            cotizacion_id=cotizacion.id,
        )

        self.assertEqual(venta.total, Decimal('80.00'))


class MetodoDePagoTests(VentaServiceTestCase):
    """VENTAS-007: una venta no puede quedarse sin forma de cobro."""

    def test_metodo_desconocido_no_crea_venta(self):
        with self.assertRaises(MetodoPagoInvalidoError):
            self._vender(metodo_pago='otro')

        self.assertFalse(Venta.objects.exists())
        self.assertEqual(
            MovimientoLote.objects.filter(tipo='VENTA').count(), 0
        )

    def test_metodo_deshabilitado_en_configuracion_es_rechazado(self):
        self._set_config(pago_transferencia=False)

        with self.assertRaises(MetodoPagoInvalidoError):
            self._vender(metodo_pago='transferencia')

        self.assertFalse(Venta.objects.exists())

    def test_venta_de_contado_registra_pagos_por_el_total(self):
        venta = self._vender()

        total_pagado = sum(p.monto for p in venta.pagos.all())
        self.assertEqual(total_pagado, venta.total)


class StockYFifoTests(VentaServiceTestCase):
    """VENTAS-003 y VENTAS-012: consumo consistente y costo persistido."""

    def test_producto_repetido_agrega_cantidades_al_validar_stock(self):
        """
        Dos líneas del mismo producto, cada una dentro del stock, pero juntas
        por encima. La validación por línea las dejaba pasar a ambas.
        """
        with self.assertRaises(StockInsuficienteError):
            self._vender(
                carrito=[
                    {'id': self.producto.id, 'cantidad': 6, 'precio_venta': '100.00'},
                    {'id': self.producto.id, 'cantidad': 6, 'precio_venta': '100.00'},
                ],
                total='1200.00',
            )

        self.assertFalse(Venta.objects.exists())

    def test_producto_repetido_dentro_del_stock_consume_todo(self):
        venta = self._vender(
            carrito=[
                {'id': self.producto.id, 'cantidad': 4, 'precio_venta': '100.00'},
                {'id': self.producto.id, 'cantidad': 3, 'precio_venta': '100.00'},
            ],
            total='700.00',
        )

        consumido = sum(
            abs(m.cantidad)
            for m in MovimientoLote.objects.filter(
                tipo='VENTA', referencia_id=venta.id
            )
        )
        self.assertEqual(consumido, 7)
        self.assertEqual(
            Lote.objects.get(producto=self.producto).cantidad_actual, 3
        )

    def test_costo_fifo_se_persiste_en_el_detalle(self):
        venta = self._vender()
        detalle = venta.detalles.get()

        # 2 unidades del único lote, a 40.00 de costo.
        self.assertEqual(detalle.costo_fifo, Decimal('80.00'))
        self.assertEqual(detalle.get_margen_bruto(), Decimal('120.00'))

    def test_costo_fifo_suma_varios_lotes(self):
        producto = self._crear_producto('VSVC-MULTI', Decimal('100.00'))
        self._ingresar_stock(producto, cantidad=2, costo=Decimal('30.00'),
                             factura='FAC-MULTI-1')
        self._ingresar_stock(producto, cantidad=5, costo=Decimal('50.00'),
                             factura='FAC-MULTI-2')

        venta = self._vender(
            carrito=[{'id': producto.id, 'cantidad': 4, 'precio_venta': '100.00'}],
            total='400.00',
        )

        # FIFO: 2 x 30.00 (lote viejo) + 2 x 50.00 (lote nuevo) = 160.00
        self.assertEqual(venta.detalles.get().costo_fifo, Decimal('160.00'))

    def test_faltante_de_fifo_aborta_la_venta_si_no_hay_inventario_negativo(self):
        """
        Sin lotes y sin inventario negativo, FIFO no puede entregar nada. Antes
        la venta se completaba igual y sólo quedaba un warning en el log.
        """
        producto_sin_stock = self._crear_producto('VSVC-SIN-STOCK', Decimal('50.00'))

        with self.assertRaises(StockInsuficienteError):
            self._vender(
                carrito=[{'id': producto_sin_stock.id, 'cantidad': 1,
                          'precio_venta': '50.00'}],
                total='50.00',
            )

        self.assertFalse(Venta.objects.exists())

    def test_inventario_negativo_permite_vender_sin_lotes(self):
        self._set_config(permitir_inventario_negativo=True)
        producto_sin_stock = self._crear_producto('VSVC-NEG', Decimal('50.00'))

        venta = self._vender(
            carrito=[{'id': producto_sin_stock.id, 'cantidad': 1,
                      'precio_venta': '50.00'}],
            total='50.00',
        )

        self.assertEqual(venta.estado, 'COMPLETADA')
        self.assertEqual(venta.detalles.get().costo_fifo, Decimal('0.00'))


class CotizacionAtomicaTests(VentaServiceTestCase):
    """VENTAS-010: la conversión ocurre dentro de la transacción de la venta."""

    def _crear_cotizacion(self):
        cotizacion = Cotizacion.objects.create(
            cliente=Cliente.get_cliente_contado(),
            usuario=self.cajera,
            total=Decimal('200.00'),
        )
        DetalleCotizacion.objects.create(
            cotizacion=cotizacion,
            producto=self.producto,
            cantidad=2,
            precio_unitario=Decimal('100.00'),
            descuento_monto=Decimal('0.00'),
            subtotal=Decimal('0.00'),
            total_linea=Decimal('0.00'),
        )
        return cotizacion

    def test_la_venta_marca_y_vincula_la_cotizacion(self):
        cotizacion = self._crear_cotizacion()

        venta = self._vender(cotizacion_id=cotizacion.id)

        cotizacion.refresh_from_db()
        self.assertEqual(cotizacion.estado, 'CONVERTIDA')
        self.assertEqual(cotizacion.venta_id, venta.id)

    def test_una_cotizacion_no_se_convierte_dos_veces(self):
        cotizacion = self._crear_cotizacion()
        self._vender(cotizacion_id=cotizacion.id)

        with self.assertRaises(CotizacionInvalidaError):
            self._vender(cotizacion_id=cotizacion.id)

        # La segunda venta no existe: no se consumió inventario dos veces.
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(
            Lote.objects.get(producto=self.producto).cantidad_actual, 8
        )

    def test_cotizacion_inexistente_es_error_de_negocio(self):
        with self.assertRaises(CotizacionInvalidaError):
            self._vender(cotizacion_id=999999)
        self.assertFalse(Venta.objects.exists())


class PrecondicionFiscalTests(VentaServiceTestCase):
    """VENTAS-011: el tipo 31 se valida antes del commit, no en el navegador."""

    def setUp(self):
        super().setUp()
        self._set_config(modulo_ecf=True)

    def test_tipo_31_sin_cliente_no_crea_venta(self):
        with self.assertRaises(TipoECFInvalidoError):
            self._vender(tipo_ecf='31')

        self.assertFalse(Venta.objects.exists())
        self.assertEqual(
            MovimientoLote.objects.filter(tipo='VENTA').count(), 0
        )

    def test_tipo_31_con_cliente_sin_rnc_no_crea_venta(self):
        cliente = Cliente.objects.create(
            tipo='CORPORATIVO',
            nombre='Cliente sin RNC',
            cedula_rnc='',
            activo=True,
        )

        with self.assertRaises(TipoECFInvalidoError):
            self._vender(tipo_ecf='31', cliente_id=cliente.id)

        self.assertFalse(Venta.objects.exists())

    def test_tipo_31_con_cliente_con_rnc_pasa(self):
        cliente = Cliente.objects.create(
            tipo='CORPORATIVO',
            nombre='Cliente con RNC',
            cedula_rnc='131-12345-6',
            activo=True,
        )

        venta = self._vender(tipo_ecf='31', cliente_id=cliente.id)

        self.assertEqual(venta.cliente_id, cliente.id)

    def test_tipo_32_sin_cliente_sigue_permitido(self):
        venta = self._vender(tipo_ecf='32')
        self.assertIsNone(venta.cliente_id)


class IdentidadDeSucursalTests(VentaServiceTestCase):
    """VENTAS-001: la venta local conserva su identidad multi-sucursal."""

    def test_venta_asigna_la_sucursal_y_prefija_su_numero(self):
        sucursal = Sucursal.objects.create(
            codigo=settings.SUCURSAL_CODIGO,
            nombre='Sucursal de prueba',
        )
        cache.clear()

        venta = self._vender()

        self.assertEqual(venta.sucursal_id, sucursal.id)
        self.assertTrue(
            venta.numero_venta.startswith(f'{sucursal.codigo}-V'),
            venta.numero_venta,
        )

    @override_settings(SYNC_ENABLED=True)
    def test_instalacion_con_sync_y_sin_sucursal_no_factura(self):
        """
        Facturar acá produciría numeración legacy que el cloud puede descartar
        por colisión con otra sucursal. Mejor parar la caja que perder ventas.
        """
        with self.assertRaises(SucursalNoResueltaError):
            self._vender()

        self.assertFalse(Venta.objects.exists())

    def test_instalacion_standalone_sin_sucursal_sigue_vendiendo(self):
        venta = self._vender()

        self.assertIsNone(venta.sucursal_id)
        self.assertTrue(venta.numero_venta.startswith('V-'), venta.numero_venta)


class NumeracionTests(VentaServiceTestCase):
    """VENTAS-002: la secuencia no se calcula contando filas."""

    def test_un_hueco_en_la_secuencia_no_reutiliza_un_numero(self):
        primera = self._vender()
        segunda = self._vender()

        self.assertTrue(primera.numero_venta.endswith('-0001'))
        self.assertTrue(segunda.numero_venta.endswith('-0002'))

        # Una corrección excepcional borra la primera venta. Con `count()+1`
        # la siguiente venta reutilizaba '-0002' y chocaba con la existente.
        numero_borrado = primera.numero_venta
        primera.detalles.all().delete()
        primera.pagos.all().delete()
        primera.delete()

        tercera = self._vender()

        self.assertTrue(tercera.numero_venta.endswith('-0003'))
        self.assertNotEqual(tercera.numero_venta, numero_borrado)
        self.assertNotEqual(tercera.numero_venta, segunda.numero_venta)
