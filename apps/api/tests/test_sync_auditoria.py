"""
apps/api/tests/test_sync_auditoria.py

Regresion del RECEPTOR cloud, para los hallazgos de
`docs/exploracion/AUDITORIA_CODIGO_APPS_SYNC.md`:

SYNC-003 (doble aplicacion bajo concurrencia), SYNC-004 (venta parcial
confirmada) y SYNC-008 (snapshot fuera de orden).
"""
import threading
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.api.views.sync import _handler_inventario_snapshot, _handler_venta_creada
from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal
from apps.sync.models import EventoSync, InventarioSucursalSnapshot
from apps.ventas.models import Venta

User = get_user_model()


def _payload_venta(numero, skus, total='200.00'):
    return {
        'numero_venta': numero,
        'sucursal_codigo': 'SD-001',
        'fecha_venta': timezone.now().isoformat(),
        'usuario_username': 'svc_auditoria',
        'cliente': None,
        'subtotal': total,
        'descuento_total': '0.00',
        'total': total,
        'estado': 'COMPLETADA',
        'condicion_pago': 'CONTADO',
        'notas': '',
        'detalles': [
            {
                'producto_sku': sku,
                'producto_nombre': sku,
                'cantidad': '1',
                'precio_unitario': '100.00',
                'subtotal': '100.00',
                'descuento_monto': '0.00',
                'descuento_porcentaje': '0.00',
                'total_linea': '100.00',
                'costo_fifo': '40.00',
            }
            for sku in skus
        ],
        'pagos': [
            {'metodo': 'EFECTIVO', 'monto': total, 'referencia': f'Efectivo - {numero}'},
        ],
    }


class _BaseCloud:
    def _montar_cloud(self):
        self.svc = User.objects.create_user(
            'svc_auditoria', 'svc_aud@test.local', 'x', rol='CAJERA',
        )
        self.sucursal = Sucursal.objects.create(
            codigo='SD-001', nombre='Sucursal SD', activa=True,
            usuario_servicio=self.svc,
        )
        self.token = Token.objects.create(user=self.svc)
        self.categoria = Categoria.objects.create(nombre='Cloud Test')

    def _producto(self, sku):
        return Producto.objects.create(
            sku=sku, codigo_barras=sku, nombre=f'Producto {sku}',
            descripcion='', categoria=self.categoria,
            precio_venta=Decimal('100.00'), stock_minimo=1, activo=True,
            estado='nuevo', marca='', atributos={},
        )

    def _api(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        return client


class VentaCompletaONadaTests(_BaseCloud, TestCase):
    """SYNC-004: una venta no puede confirmarse con lineas omitidas."""

    def setUp(self):
        self._montar_cloud()

    def test_sku_ausente_falla_el_evento_entero(self):
        self._producto('CLOUD-A')
        payload = _payload_venta('V-CLOUD-0001', ['CLOUD-A', 'CLOUD-FALTANTE'])

        with self.assertRaises(ValueError) as ctx:
            _handler_venta_creada(self.sucursal, payload)

        self.assertIn('CLOUD-FALTANTE', str(ctx.exception))
        # Nada a medias: la cabecera tampoco se creo.
        self.assertFalse(Venta.objects.filter(numero_venta='V-CLOUD-0001').exists())

    def test_el_endpoint_reporta_error_y_no_confirma_el_evento(self):
        self._producto('CLOUD-A')
        payload = _payload_venta('V-CLOUD-0002', ['CLOUD-A', 'CLOUD-FALTANTE'])

        res = self._api().post(
            '/api/v1/sync/eventos/',
            {'eventos': [{
                'tipo_evento': 'VENTA_CREADA',
                'payload': payload,
                'hash_payload': 'hash_venta_parcial',
                'timestamp': timezone.now().isoformat(),
            }]},
            format='json',
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['errores'], 1)
        self.assertEqual(res.data['detalle'][0]['estado'], 'ERROR')

        # El evento NO queda registrado: el reintento posterior lo aplica
        # completo cuando el producto llegue.
        self.assertFalse(
            EventoSync.objects.filter(hash_payload='hash_venta_parcial').exists()
        )
        self.assertFalse(Venta.objects.filter(numero_venta='V-CLOUD-0002').exists())

    def test_cuando_el_producto_llega_el_reintento_aplica_la_venta_completa(self):
        self._producto('CLOUD-A')
        payload = _payload_venta('V-CLOUD-0003', ['CLOUD-A', 'CLOUD-TARDIO'])

        with self.assertRaises(ValueError):
            _handler_venta_creada(self.sucursal, payload)

        self._producto('CLOUD-TARDIO')
        _handler_venta_creada(self.sucursal, payload)

        venta = Venta.objects.get(numero_venta='V-CLOUD-0003')
        self.assertEqual(venta.detalles.count(), 2)
        self.assertEqual(venta.pagos.count(), 1)

    def test_reenvio_correctivo_reconstruye_lineas_faltantes(self):
        """
        Repara ventas que quedaron partidas ANTES de este fix: el reenvio ya no
        solo enlaza el cliente, tambien completa detalles y pagos.
        """
        self._producto('CLOUD-A')
        self._producto('CLOUD-B')
        payload = _payload_venta('V-CLOUD-0004', ['CLOUD-A', 'CLOUD-B'])

        _handler_venta_creada(self.sucursal, payload)
        venta = Venta.objects.get(numero_venta='V-CLOUD-0004')

        # Simula el dano historico: una linea perdida.
        venta.detalles.first().delete()
        self.assertEqual(venta.detalles.count(), 1)

        _handler_venta_creada(self.sucursal, payload)

        venta.refresh_from_db()
        self.assertEqual(venta.detalles.count(), 2)
        self.assertEqual(venta.pagos.count(), 1)


class SnapshotFueraDeOrdenTests(_BaseCloud, TestCase):
    """SYNC-008: un snapshot viejo no puede pisar uno mas reciente."""

    def setUp(self):
        self._montar_cloud()

    def _snapshot(self, timestamp, stock):
        return {
            'sucursal_codigo': 'SD-001',
            'timestamp': timestamp.isoformat(),
            'items': [{
                'producto_sku': 'SNAP-001',
                'producto_nombre': 'Producto snapshot',
                'stock_actual': stock,
                'stock_minimo': 5,
                'bajo_stock': stock < 5,
                'valor_fifo': str(Decimal(stock) * Decimal('40.00')),
            }],
        }

    def test_t1_despues_de_t2_no_retrocede_el_stock(self):
        t1 = timezone.now()
        t2 = t1 + timedelta(minutes=5)

        _handler_inventario_snapshot(self.sucursal, self._snapshot(t2, 3))
        _handler_inventario_snapshot(self.sucursal, self._snapshot(t1, 99))

        fila = InventarioSucursalSnapshot.objects.get(producto_sku='SNAP-001')
        self.assertEqual(fila.stock_actual, 3)
        self.assertEqual(fila.timestamp, t2)

    def test_un_snapshot_mas_nuevo_si_se_aplica(self):
        t1 = timezone.now()
        t2 = t1 + timedelta(minutes=5)

        _handler_inventario_snapshot(self.sucursal, self._snapshot(t1, 99))
        _handler_inventario_snapshot(self.sucursal, self._snapshot(t2, 3))

        fila = InventarioSucursalSnapshot.objects.get(producto_sku='SNAP-001')
        self.assertEqual(fila.stock_actual, 3)


class DeduplicacionConcurrenteTests(_BaseCloud, TransactionTestCase):
    """
    SYNC-003: dos requests con el mismo hash no pueden aplicar el efecto dos
    veces.

    Necesita `TransactionTestCase`: la carrera solo existe si las dos
    transacciones son reales y se ven entre si.
    """

    def setUp(self):
        self._montar_cloud()
        self._producto('CONC-A')

    def tearDown(self):
        connection.close()

    def test_dos_requests_simultaneas_con_el_mismo_hash_aplican_una_sola_vez(self):
        payload = _payload_venta('V-CONC-0001', ['CONC-A'])
        cuerpo = {'eventos': [{
            'tipo_evento': 'VENTA_CREADA',
            'payload': payload,
            'hash_payload': 'hash_concurrente',
            'timestamp': timezone.now().isoformat(),
        }]}

        barrera = threading.Barrier(2, timeout=30)
        resultados = []
        candado = threading.Lock()

        def enviar():
            try:
                barrera.wait()
                res = self._api().post(
                    '/api/v1/sync/eventos/', cuerpo, format='json',
                )
                with candado:
                    resultados.append(res.data)
            finally:
                connection.close()

        hilos = [threading.Thread(target=enviar) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=30)

        self.assertEqual(len(resultados), 2, 'algun hilo no termino')

        # Lo que importa no es cual gano, sino que el hecho se aplico UNA vez.
        self.assertEqual(Venta.objects.filter(numero_venta='V-CONC-0001').count(), 1)
        self.assertEqual(
            EventoSync.objects.filter(hash_payload='hash_concurrente').count(), 1
        )

        venta = Venta.objects.get(numero_venta='V-CONC-0001')
        self.assertEqual(venta.detalles.count(), 1)
        self.assertEqual(venta.pagos.count(), 1)

        # Para la sucursal ambos envios cuentan como entregados: ninguno
        # devuelve ERROR, asi que el evento no se reintenta en vano.
        estados = [r['detalle'][0]['estado'] for r in resultados]
        self.assertEqual(
            sorted(estados), ['CONFIRMADO', 'DUPLICADO'], f'estados={estados}'
        )
