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


class VentaConStubsTests(_BaseCloud, TestCase):
    """
    BUG-G (docs/BUGS.md): un SKU que no existe en cloud ya no rechaza la
    venta entera. Nace como stub sellado (origen_sucursal +
    pendiente_revision) y la venta se aplica completa -- antes, dos ventas de
    produccion murieron DESCARTADAS tras 10 reintentos porque el producto
    jamas se creaba en cloud por ningun otro camino.
    """

    def setUp(self):
        self._montar_cloud()

    def test_sku_faltante_crea_un_stub_y_la_venta_se_aplica_completa(self):
        self._producto('CLOUD-A')
        payload = _payload_venta('V-CLOUD-0001', ['CLOUD-A', 'CLOUD-FALTANTE'])

        _handler_venta_creada(self.sucursal, payload)

        venta = Venta.objects.get(numero_venta='V-CLOUD-0001')
        self.assertEqual(venta.detalles.count(), 2)

        stub = Producto.objects.get(sku='CLOUD-FALTANTE')
        self.assertEqual(stub.nombre, 'CLOUD-FALTANTE')  # producto_nombre del payload
        self.assertEqual(stub.precio_venta, Decimal('100.00'))  # precio_unitario del payload
        self.assertEqual(stub.categoria.nombre, 'Sin clasificar')
        self.assertTrue(stub.activo)
        self.assertTrue(stub.pendiente_revision)
        self.assertEqual(stub.origen_sucursal_id, self.sucursal.id)

    def test_el_endpoint_confirma_el_evento_con_stub(self):
        self._producto('CLOUD-A')
        payload = _payload_venta('V-CLOUD-0002', ['CLOUD-A', 'CLOUD-FALTANTE'])

        res = self._api().post(
            '/api/v1/sync/eventos/',
            {'eventos': [{
                'tipo_evento': 'VENTA_CREADA',
                'payload': payload,
                'hash_payload': 'hash_venta_con_stub',
                'timestamp': timezone.now().isoformat(),
            }]},
            format='json',
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['errores'], 0)
        self.assertEqual(res.data['detalle'][0]['estado'], 'CONFIRMADO')
        self.assertTrue(
            EventoSync.objects.filter(hash_payload='hash_venta_con_stub').exists()
        )
        self.assertTrue(Venta.objects.filter(numero_venta='V-CLOUD-0002').exists())

    def test_dos_ventas_con_el_mismo_sku_faltante_no_duplican_el_stub(self):
        payload_1 = _payload_venta('V-CLOUD-0003', ['CLOUD-COMPARTIDO'])
        payload_2 = _payload_venta('V-CLOUD-0004', ['CLOUD-COMPARTIDO'])

        _handler_venta_creada(self.sucursal, payload_1)
        _handler_venta_creada(self.sucursal, payload_2)

        self.assertEqual(Producto.objects.filter(sku='CLOUD-COMPARTIDO').count(), 1)
        stub = Producto.objects.get(sku='CLOUD-COMPARTIDO')
        self.assertEqual(
            Venta.objects.get(numero_venta='V-CLOUD-0003').detalles.get().producto_id,
            stub.id,
        )
        self.assertEqual(
            Venta.objects.get(numero_venta='V-CLOUD-0004').detalles.get().producto_id,
            stub.id,
        )

    def test_linea_sin_sku_sigue_siendo_error(self):
        """Payload corrupto/legacy: no hay con que crear ni el stub."""
        payload = _payload_venta('V-CLOUD-0005', ['CLOUD-A'])
        payload['detalles'][0]['producto_sku'] = None

        with self.assertRaises(ValueError):
            _handler_venta_creada(self.sucursal, payload)
        self.assertFalse(Venta.objects.filter(numero_venta='V-CLOUD-0005').exists())

    def test_reparar_lineas_venta_tambien_crea_stubs_sellados(self):
        """El camino correctivo (_reparar_lineas_venta) usa la sucursal de la venta."""
        self._producto('CLOUD-A')
        payload = _payload_venta('V-CLOUD-0006', ['CLOUD-A'])
        _handler_venta_creada(self.sucursal, payload)

        venta = Venta.objects.get(numero_venta='V-CLOUD-0006')
        venta.detalles.all().delete()

        payload_reparado = _payload_venta('V-CLOUD-0006', ['CLOUD-A', 'CLOUD-REPARADO'])
        _handler_venta_creada(self.sucursal, payload_reparado)

        venta.refresh_from_db()
        self.assertEqual(venta.detalles.count(), 2)
        stub = Producto.objects.get(sku='CLOUD-REPARADO')
        self.assertTrue(stub.pendiente_revision)
        self.assertEqual(stub.origen_sucursal_id, self.sucursal.id)

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


class LedgerCompraUnaAutoridadTests(_BaseCloud, TestCase):
    """
    INVENTARIO-007: una compra escribia el ledger cloud por DOS caminos.

    `COMPRA_REGISTRADA` creaba una fila por linea con `movimiento_id_local=None`
    y `INVENTARIO_MOVIMIENTO_REGISTRADO` otra con el ID real. Cada linea quedaba
    duplicada, y al corregir la compra solo se actualizaba la que tenia ID: las
    dos versiones divergian.
    """

    def setUp(self):
        self._montar_cloud()
        self._producto('LEDGER-A')

    def _payload_compra(self):
        return {
            'compra_id_local': 55,
            'numero_compra': 'COMP-20260820-00001',
            'fecha_compra': timezone.now().isoformat(),
            'usuario_username': 'svc_auditoria',
            'detalles': [{
                'producto_sku': 'LEDGER-A',
                'producto_nombre': 'Producto LEDGER-A',
                'lote_numero': 'LOTE-20260820-00001',
                'cantidad': '4',
                'costo_unitario': '10.00',
            }],
        }

    def _payload_movimiento(self):
        return {
            'movimiento_id_local': 900,
            'tipo': 'COMPRA',
            'producto_sku': 'LEDGER-A',
            'producto_nombre': 'Producto LEDGER-A',
            'lote_numero': 'LOTE-20260820-00001',
            'cantidad': 4,
            'cantidad_anterior': 0,
            'cantidad_nueva': 4,
            'costo_unitario': '10.00',
            'referencia_tipo': 'Compra',
            'referencia_id': 55,
            'usuario_username': 'svc_auditoria',
            'notas': 'Compra inicial',
            'fecha_movimiento': timezone.now().isoformat(),
        }

    def test_una_compra_deja_exactamente_una_fila_de_ledger_por_linea(self):
        from apps.api.views.sync import (
            _handler_compra,
            _handler_movimiento_inventario,
        )
        from apps.sync.models import InventarioMovimientoSync

        # Los dos eventos que la sucursal emite por una compra, en orden.
        _handler_compra(self.sucursal, self._payload_compra())
        _handler_movimiento_inventario(self.sucursal, self._payload_movimiento())

        filas = InventarioMovimientoSync.objects.filter(producto_sku='LEDGER-A')

        self.assertEqual(
            filas.count(), 1,
            list(filas.values('movimiento_id_local', 'cantidad')),
        )
        # La que sobrevive es la que tiene identidad estable.
        self.assertEqual(filas.get().movimiento_id_local, 900)

    def test_corregir_la_compra_actualiza_esa_unica_fila(self):
        from apps.api.views.sync import (
            _handler_compra,
            _handler_movimiento_inventario,
        )
        from apps.sync.models import InventarioMovimientoSync

        _handler_compra(self.sucursal, self._payload_compra())
        _handler_movimiento_inventario(self.sucursal, self._payload_movimiento())

        # La sucursal corrige la cantidad y reenvia el movimiento.
        corregido = {**self._payload_movimiento(), 'cantidad': 9, 'cantidad_nueva': 9}
        _handler_movimiento_inventario(self.sucursal, corregido)

        filas = InventarioMovimientoSync.objects.filter(producto_sku='LEDGER-A')
        self.assertEqual(filas.count(), 1)
        self.assertEqual(filas.get().cantidad, 9)


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
