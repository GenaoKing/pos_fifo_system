"""
apps/cotizaciones/tests/test_auditoria_cotizaciones.py

Regresion de los hallazgos de
`docs/exploracion/AUDITORIA_CODIGO_APPS_COTIZACIONES.md`.
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.clientes.models import Cliente
from apps.cotizaciones.models import Cotizacion, DetalleCotizacion
from apps.inventario.models import Compra, DetalleCompra
from apps.permisos import testing as permisos_testing
from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal
from apps.ventas.services import procesar_venta_service
from apps.ventas.services.exceptions import CotizacionInvalidaError

User = get_user_model()

COTIZAR = ['ventas.crear', 'cotizaciones.ver', 'cotizaciones.crear']


class CotizacionesTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.negocio = permisos_testing.crear_negocio('Negocio COT')
        self.suc_a = Sucursal.objects.create(
            codigo='COT-A', nombre='Tienda A', activa=True, negocio=self.negocio,
        )
        self.suc_b = Sucursal.objects.create(
            codigo='COT-B', nombre='Tienda B', activa=True, negocio=self.negocio,
        )
        self.categoria = Categoria.objects.create(nombre='Cotizables', activa=True)
        self.producto = Producto.objects.create(
            sku='COT-001', codigo_barras='COT-001', nombre='Tuberia',
            descripcion='', categoria=self.categoria,
            precio_venta=Decimal('100.00'), stock_minimo=1, activo=True,
            estado='nuevo', marca='', atributos={},
        )
        # El handler de sync cae al usuario de servicio cuando el username del
        # payload no resuelve; sin el, la cotizacion queda sin `usuario`.
        self.svc = User.objects.create_user(
            username='svc_cot', email='svc_cot@test.local', password='x',
            rol='CAJERA', activo=True,
        )
        self.suc_a.usuario_servicio = self.svc
        self.suc_a.save(update_fields=['usuario_servicio'])

        self.cliente = Cliente.objects.create(nombre='Constructora', tipo='CORPORATIVO')
        self.otro_cliente = Cliente.objects.create(nombre='Otro', tipo='PERSONAL')

    def tearDown(self):
        cache.clear()

    def _usuario(self, username, permisos=COTIZAR, rol='CAJERA'):
        user = User.objects.create_user(
            username=username, email=f'{username}@test.local',
            password='Prueba123', rol=rol, activo=True,
        )
        permisos_testing.habilitar_cajero(user, permisos=list(permisos))
        return user

    def _stock(self, cantidad=50):
        compra = Compra.objects.create(
            usuario=self._usuario('comprador', permisos=['ventas.crear']),
            proveedor='Prov', numero_factura=f'F-COT-{cantidad}',
            total=Decimal('500.00'),
        )
        DetalleCompra.objects.create(
            compra=compra, producto=self.producto, cantidad=cantidad,
            costo_unitario=Decimal('10.00'), subtotal=Decimal('500.00'),
        )

    def _cotizar(self, user, precio='100.00', cantidad=1):
        self.client.force_login(user)
        return self.client.post(
            reverse('cotizaciones:api_guardar'),
            data=json.dumps({
                'cliente_id': self.cliente.id,
                'productos': [{
                    'producto_id': self.producto.id,
                    'cantidad': cantidad,
                    'precio_unitario': precio,
                    'descuento': 0,
                }],
            }),
            content_type='application/json',
        )

    def _cotizacion_directa(self, precio='50.00', cantidad=1,
                            cliente=None, sucursal=None):
        """Crea la fila sin pasar por la vista (para probar la conversion)."""
        cotizacion = Cotizacion.objects.create(
            cliente=cliente or self.cliente,
            usuario=self._usuario(f'emisor_{Cotizacion.objects.count()}'),
            sucursal=sucursal if sucursal is not None else self.suc_a,
            total=Decimal(precio) * cantidad,
        )
        DetalleCotizacion.objects.create(
            cotizacion=cotizacion, producto=self.producto, cantidad=cantidad,
            precio_unitario=Decimal(precio), descuento_monto=Decimal('0'),
            subtotal=Decimal(precio) * cantidad,
            total_linea=Decimal(precio) * cantidad,
        )
        return cotizacion


class GatesDelModuloTests(CotizacionesTestCase):
    """COT-001: el modulo tiene permisos y los aplica."""

    def test_sin_permiso_no_se_lista(self):
        """
        La reproduccion: cualquier usuario autenticado con el modulo habilitado
        podia ver, crear, descargar y marcar cotizaciones como convertidas.
        """
        pelado = self._usuario('sin_cot', permisos=['ventas.crear'])
        self.client.force_login(pelado)

        respuesta = self.client.get(reverse('cotizaciones:lista'))

        self.assertEqual(respuesta.status_code, 302)

    def test_sin_permiso_no_se_crea(self):
        pelado = self._usuario('sin_crear_cot', permisos=['ventas.crear'])

        respuesta = self._cotizar(pelado)

        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(Cotizacion.objects.count(), 0)

    def test_con_permiso_si_se_crea(self):
        autorizado = self._usuario('con_cot')

        respuesta = self._cotizar(autorizado)

        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(Cotizacion.objects.count(), 1)


class PrecioServerSideTests(CotizacionesTestCase):
    """COT-002: el precio cotizado lo decide el servidor."""

    def test_un_precio_inventado_se_rechaza(self):
        """
        La reproduccion completa: una cajera con `ventas.crear` y SIN
        `ventas.aplicar_descuento` guardo una cotizacion de una unidad a
        RD$0.01 y despues vendio cinco a ese precio. El descuento real quedaba
        disfrazado de "precio cotizado".
        """
        cajera = self._usuario('cajera_cot')

        respuesta = self._cotizar(cajera, precio='0.01')

        self.assertEqual(respuesta.status_code, 403)
        self.assertIn('precio_negociado', respuesta.json()['error'])
        self.assertEqual(Cotizacion.objects.count(), 0)

    def test_el_precio_vigente_siempre_pasa(self):
        cajera = self._usuario('cajera_cot2')

        respuesta = self._cotizar(cajera, precio='100.00')

        self.assertEqual(respuesta.status_code, 200, respuesta.content)

    def test_con_el_permiso_si_se_negocia(self):
        negociador = self._usuario(
            'negociador', permisos=[*COTIZAR, 'cotizaciones.precio_negociado'],
        )

        respuesta = self._cotizar(negociador, precio='80.00')

        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        detalle = DetalleCotizacion.objects.get()
        self.assertEqual(detalle.precio_unitario, Decimal('80.00'))

    def test_cotizar_mas_caro_no_necesita_permiso(self):
        """Un recargo no es un descuento encubierto."""
        cajera = self._usuario('cajera_cot3')

        respuesta = self._cotizar(cajera, precio='150.00')

        self.assertEqual(respuesta.status_code, 200, respuesta.content)

    def test_sin_precio_se_usa_el_vigente(self):
        cajera = self._usuario('cajera_cot4')
        self.client.force_login(cajera)

        respuesta = self.client.post(
            reverse('cotizaciones:api_guardar'),
            data=json.dumps({
                'cliente_id': self.cliente.id,
                'productos': [{
                    'producto_id': self.producto.id, 'cantidad': 2,
                }],
            }),
            content_type='application/json',
        )

        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(
            DetalleCotizacion.objects.get().precio_unitario, Decimal('100.00'),
        )


class AlcanceDeLaConversionTests(CotizacionesTestCase):
    """COT-003: la oferta vale para quien, donde y cuanto se cotizo."""

    def setUp(self):
        super().setUp()
        self._stock()
        self.vendedor = self._usuario('vendedor_cot')

    def _vender(self, cotizacion, cantidad=1, cliente=None, precio='50.00'):
        return procesar_venta_service(
            usuario=self.vendedor,
            datos={
                'carrito': [{
                    'id': self.producto.id, 'cantidad': cantidad,
                    'precio_venta': precio, 'descuento': '0.00',
                }],
                'metodo_pago': 'efectivo',
                'total': str(Decimal(precio) * cantidad),
                'cliente_id': (cliente or self.cliente).id,
                'cotizacion_id': cotizacion.id,
            },
        )

    def test_no_se_venden_mas_unidades_de_las_cotizadas(self):
        """
        La reproduccion: una cotizacion para UNA unidad autorizo CINCO al mismo
        precio negociado.
        """
        cotizacion = self._cotizacion_directa(precio='50.00', cantidad=1)

        with self.assertRaises(CotizacionInvalidaError) as ctx:
            self._vender(cotizacion, cantidad=5)

        self.assertIn('autoriza 1', str(ctx.exception))

    def test_la_cantidad_cotizada_si_se_vende(self):
        cotizacion = self._cotizacion_directa(precio='50.00', cantidad=3)

        venta = self._vender(cotizacion, cantidad=3)

        self.assertEqual(venta.total, Decimal('150.00'))

    def test_no_se_convierte_para_otro_cliente(self):
        """La reproduccion: una cotizacion de A se convirtio en venta de B."""
        cotizacion = self._cotizacion_directa(cliente=self.cliente)

        with self.assertRaises(CotizacionInvalidaError) as ctx:
            self._vender(cotizacion, cliente=self.otro_cliente)

        self.assertIn('otro cliente', str(ctx.exception))

    def test_no_se_convierte_en_otra_sucursal(self):
        cotizacion = self._cotizacion_directa(sucursal=self.suc_b)

        with self.settings(SUCURSAL_CODIGO='COT-A'):
            cache.clear()
            with self.assertRaises(CotizacionInvalidaError) as ctx:
                self._vender(cotizacion)

        self.assertIn('otra sucursal', str(ctx.exception))


class VigenciaTests(CotizacionesTestCase):
    """COT-007: los 15 dias del PDF los sostiene el backend."""

    def test_una_cotizacion_vencida_no_se_convierte(self):
        """
        El PDF afirma "valida por 15 dias", pero el modelo solo miraba el
        estado: un precio historico quedaba convertible indefinidamente.
        """
        cotizacion = self._cotizacion_directa()
        Cotizacion.objects.filter(pk=cotizacion.pk).update(
            fecha_creacion=timezone.now() - timedelta(days=20),
        )
        cotizacion.refresh_from_db()

        self.assertTrue(cotizacion.esta_vencida)
        self.assertFalse(cotizacion.puede_convertirse)

    def test_dentro_de_la_vigencia_si(self):
        cotizacion = self._cotizacion_directa()
        Cotizacion.objects.filter(pk=cotizacion.pk).update(
            fecha_creacion=timezone.now() - timedelta(days=5),
        )
        cotizacion.refresh_from_db()

        self.assertFalse(cotizacion.esta_vencida)
        self.assertTrue(cotizacion.puede_convertirse)

    def test_el_mensaje_nombra_el_vencimiento(self):
        self._stock()
        cotizacion = self._cotizacion_directa()
        Cotizacion.objects.filter(pk=cotizacion.pk).update(
            fecha_creacion=timezone.now() - timedelta(days=20),
        )

        with self.assertRaises(CotizacionInvalidaError) as ctx:
            procesar_venta_service(
                usuario=self._usuario('vendedor_venc'),
                datos={
                    'carrito': [{
                        'id': self.producto.id, 'cantidad': 1,
                        'precio_venta': '50.00', 'descuento': '0.00',
                    }],
                    'metodo_pago': 'efectivo', 'total': '50.00',
                    'cliente_id': self.cliente.id,
                    'cotizacion_id': cotizacion.id,
                },
            )

        self.assertIn('vencio', str(ctx.exception))


class AlcanceDeLasVistasTests(CotizacionesTestCase):
    """COT-005: las cotizaciones no cruzan de sucursal."""

    def setUp(self):
        super().setUp()
        self.de_b = self._cotizacion_directa(sucursal=self.suc_b)
        self.operador = self._usuario('operador_a')

    def test_no_se_abre_el_detalle_de_otra_sucursal(self):
        self.client.force_login(self.operador)

        with self.settings(SUCURSAL_CODIGO='COT-A'):
            cache.clear()
            respuesta = self.client.get(
                reverse('cotizaciones:detalle', args=[self.de_b.id]),
            )

        self.assertEqual(respuesta.status_code, 404)

    def test_no_se_carga_en_el_pos_la_de_otra_sucursal(self):
        """La superficie mas sensible: esta carga los precios negociados."""
        self.client.force_login(self.operador)

        with self.settings(SUCURSAL_CODIGO='COT-A'):
            cache.clear()
            respuesta = self.client.get(
                reverse('cotizaciones:api_datos', args=[self.de_b.id]),
            )

        self.assertEqual(respuesta.status_code, 404)

    def test_el_listado_no_la_incluye(self):
        self.client.force_login(self.operador)

        with self.settings(SUCURSAL_CODIGO='COT-A'):
            cache.clear()
            respuesta = self.client.get(reverse('cotizaciones:lista'))

        self.assertNotIn(self.de_b, respuesta.context['cotizaciones'])


class EndpointLegacyTests(CotizacionesTestCase):
    """COT-006: marcar convertida deja de aceptar cualquier cosa."""

    def setUp(self):
        super().setUp()
        self.cotizacion = self._cotizacion_directa()
        self.operador = self._usuario('operador_legacy')
        self.client.force_login(self.operador)

    def _marcar(self, **cuerpo):
        return self.client.post(
            reverse('cotizaciones:api_convertida', args=[self.cotizacion.id]),
            data=json.dumps(cuerpo), content_type='application/json',
        )

    def test_no_se_marca_convertida_sin_venta(self):
        """
        La cotizacion quedaba CONVERTIDA con `venta=NULL`: la oferta se cerraba
        sin que existiera la operacion que supuestamente la consumio.
        """
        respuesta = self._marcar()

        self.assertEqual(respuesta.status_code, 400)
        self.cotizacion.refresh_from_db()
        self.assertEqual(self.cotizacion.estado, 'PENDIENTE')

    def test_no_se_vincula_una_venta_de_otro_cliente(self):
        from apps.ventas.models import Venta

        ajena = Venta.objects.create(
            usuario=self.operador, cliente=self.otro_cliente,
            sucursal=self.suc_a, subtotal=Decimal('50.00'),
            total=Decimal('50.00'), estado='COMPLETADA',
            condicion_pago='CONTADO',
        )

        respuesta = self._marcar(venta_id=ajena.id)

        self.assertEqual(respuesta.status_code, 409)
        self.cotizacion.refresh_from_db()
        self.assertEqual(self.cotizacion.estado, 'PENDIENTE')

    def test_no_se_reutiliza_una_venta_ya_vinculada(self):
        from apps.ventas.models import Venta

        venta = Venta.objects.create(
            usuario=self.operador, cliente=self.cliente, sucursal=self.suc_a,
            subtotal=Decimal('50.00'), total=Decimal('50.00'),
            estado='COMPLETADA', condicion_pago='CONTADO',
        )
        otra = self._cotizacion_directa()
        otra.venta = venta
        otra.estado = 'CONVERTIDA'
        otra.save()

        respuesta = self._marcar(venta_id=venta.id)

        self.assertEqual(respuesta.status_code, 409)

    def test_una_venta_propia_si_vincula(self):
        from apps.ventas.models import Venta

        venta = Venta.objects.create(
            usuario=self.operador, cliente=self.cliente, sucursal=self.suc_a,
            subtotal=Decimal('50.00'), total=Decimal('50.00'),
            estado='COMPLETADA', condicion_pago='CONTADO',
        )

        respuesta = self._marcar(venta_id=venta.id)

        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.cotizacion.refresh_from_db()
        self.assertEqual(self.cotizacion.estado, 'CONVERTIDA')
        self.assertEqual(self.cotizacion.venta_id, venta.id)


class EventoAtrasadoTests(CotizacionesTestCase):
    """COT-004: un CREADA viejo no reabre una cotizacion convertida."""

    def test_el_handler_no_retrocede_el_estado(self):
        """
        La reproduccion: se capturo el payload inicial, se convirtio la
        cotizacion y se aplico despues ese CREADA antiguo. El cloud la dejo
        PENDIENTE conservando la primera venta, y una segunda llamada creo OTRA
        venta contra la misma oferta.
        """
        from apps.api.views.sync import _handler_cotizacion_creada

        cotizacion = self._cotizacion_directa()
        payload = {
            'numero_cotizacion': cotizacion.numero_cotizacion,
            'cliente_nombre': self.cliente.nombre,
            'usuario_username': 'quien_sea',
            'subtotal': '50.00', 'descuento_total': '0.00', 'total': '50.00',
            'estado': 'PENDIENTE',
            'detalles': [],
        }

        cotizacion.estado = 'CONVERTIDA'
        cotizacion.save(update_fields=['estado'])

        _handler_cotizacion_creada(self.suc_a, payload)

        cotizacion.refresh_from_db()
        self.assertEqual(cotizacion.estado, 'CONVERTIDA')

    def test_sobre_una_pendiente_si_actualiza(self):
        from apps.api.views.sync import _handler_cotizacion_creada

        cotizacion = self._cotizacion_directa()
        payload = {
            'numero_cotizacion': cotizacion.numero_cotizacion,
            'cliente_nombre': self.cliente.nombre,
            'usuario_username': 'quien_sea',
            'subtotal': '77.00', 'descuento_total': '0.00', 'total': '77.00',
            'estado': 'PENDIENTE',
            'detalles': [],
        }

        _handler_cotizacion_creada(self.suc_a, payload)

        cotizacion.refresh_from_db()
        self.assertEqual(cotizacion.total, Decimal('77.00'))
