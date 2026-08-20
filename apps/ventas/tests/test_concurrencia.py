"""
apps/ventas/tests/test_concurrencia.py

Carreras reales entre dos cajas. Necesitan `TransactionTestCase` y conexiones
separadas: un `TestCase` normal envuelve todo en una transacción que nunca
commitea, así que los hilos no se ven entre sí y la carrera no se reproduce.

Hallazgos cubiertos (docs/exploracion/AUDITORIA_CODIGO_APPS_VENTAS.md):
VENTAS-002 (numeración), VENTAS-003 (consumo FIFO), VENTAS-008 (doble anulación).
"""
import threading
import time
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase

from apps.configuracion.utils import get_config
from apps.inventario.models import Compra, DetalleCompra, Lote, MovimientoLote
from apps.permisos.testing import habilitar_cajero
from apps.productos.models import Categoria, Producto
from apps.ventas.models import Venta
from apps.ventas.services import anular_venta_service, procesar_venta_service


class ConcurrenciaTestCase(TransactionTestCase):
    # OJO: TransactionTestCase hace TRUNCATE al terminar, y eso borra el
    # catalogo de permisos sembrado por la data migration de apps.permisos.
    # No se usa `serialized_rollback` (choca con los content types al
    # redeserializar): el runner de Django corre estos casos DESPUES de los
    # TestCase normales, y `permisos.testing.crear_rol` resiembra el catalogo
    # de forma idempotente en cada fixture.

    def setUp(self):
        cache.clear()

        User = get_user_model()
        self.admin = User.objects.create_user(
            username='admin_concurrencia',
            email='admin_concurrencia@test.local',
            password='pass',
            rol='ADMIN',
            activo=True,
        )
        self.cajera = User.objects.create_user(
            username='cajera_concurrencia',
            email='cajera_concurrencia@test.local',
            password='pass',
            rol='CAJERA',
            activo=True,
        )
        habilitar_cajero(self.cajera)

        self.categoria = Categoria.objects.create(nombre='Concurrencia Test')
        self.producto = Producto.objects.create(
            sku='CONC-001',
            codigo_barras='CONC-001',
            nombre='Producto concurrencia',
            descripcion='',
            categoria=self.categoria,
            precio_venta=Decimal('100.00'),
            stock_minimo=1,
            activo=True,
            estado='nuevo',
            marca='',
            atributos={},
        )
        compra = Compra.objects.create(
            usuario=self.admin,
            proveedor='Proveedor Concurrencia',
            numero_factura='FAC-CONC-001',
            total=Decimal('200.00'),
        )
        DetalleCompra.objects.create(
            compra=compra,
            producto=self.producto,
            cantidad=5,
            costo_unitario=Decimal('40.00'),
            subtotal=Decimal('200.00'),
        )

        # Materializa la ConfiguracionNegocio ANTES de lanzar los hilos: si la
        # crean ellos, la carrera bajo prueba se confunde con la del singleton
        # de configuracion (que en produccion ya existe).
        get_config()

    def tearDown(self):
        cache.clear()

    # -- helpers ------------------------------------------------------------

    def _en_paralelo(self, objetivo, veces=2, timeout=30):
        """
        Corre `objetivo(indice)` en `veces` hilos, sincronizados con una barrera
        para que se solapen de verdad. Devuelve la lista de resultados.

        Cada hilo cierra su conexión al terminar: Django abre una por hilo y
        TransactionTestCase no las limpia solo.
        """
        barrera = threading.Barrier(veces, timeout=timeout)
        resultados = []
        candado = threading.Lock()

        def correr(indice):
            try:
                barrera.wait()
                resultado = objetivo(indice)
                with candado:
                    resultados.append(('ok', resultado))
            except Exception as exc:
                with candado:
                    resultados.append(('error', type(exc).__name__))
            finally:
                connection.close()

        hilos = [threading.Thread(target=correr, args=(i,)) for i in range(veces)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=timeout)

        self.assertEqual(len(resultados), veces, 'algún hilo no terminó a tiempo')
        return resultados

    def _vender(self, cantidad):
        return procesar_venta_service(
            usuario=self.cajera,
            datos={
                'carrito': [{
                    'id': self.producto.id,
                    'cantidad': cantidad,
                    'precio_venta': '100.00',
                    'descuento': '0.00',
                }],
                'metodo_pago': 'efectivo',
                'total': str(Decimal('100.00') * cantidad),
            },
        )


class ConsumoFifoConcurrenteTests(ConcurrenciaTestCase):
    """VENTAS-003: dos ventas simultáneas no pueden consumir el mismo lote."""

    def test_consumo_fifo_simultaneo_no_pierde_actualizaciones(self):
        """
        Prueba dirigida del lock, al nivel donde vive la carrera.

        `procesar_venta_fifo` hace read-modify-write sobre `Lote.cantidad_actual`.
        Se ensancha la ventana entre la lectura y la escritura (save lento) y se
        lanzan dos consumos simultáneos del mismo producto. La invariante que se
        verifica es contable, no de resultado:

            cantidad_inicial - suma(movimientos de VENTA) == Lote.cantidad_actual

        Sin `select_for_update()` ambos hilos leen 5, ambos escriben 5-3=2 y la
        invariante se rompe (5-6 != 2): stock inventado y movimientos que no
        cuadran con el lote.

        NOTA: esta carrera NO se reproduce vendiendo por `procesar_venta_service`
        en dos hilos, porque el índice único de `numero_venta` los serializa en
        el INSERT de la cabecera. Esa serialización es accidental (desaparecería
        si la numeración pasara a una secuencia), así que el lock se prueba acá.
        """
        from apps.inventario.fifo_logic import procesar_venta_fifo

        original_save = Lote.save

        def save_lento(self, *args, **kwargs):
            time.sleep(0.4)
            return original_save(self, *args, **kwargs)

        with patch.object(Lote, 'save', save_lento):
            self._en_paralelo(
                lambda i: procesar_venta_fifo(
                    producto_id=self.producto.id,
                    cantidad_solicitada=3,
                    venta_id=9000 + i,
                    usuario=self.cajera,
                )['cantidad_vendida']
            )

        lote = Lote.objects.get(producto=self.producto)
        consumido = sum(
            abs(m.cantidad)
            for m in MovimientoLote.objects.filter(tipo='VENTA')
        )

        self.assertEqual(
            5 - consumido,
            lote.cantidad_actual,
            f'lote={lote.cantidad_actual}, movimientos={consumido}',
        )
        self.assertEqual(lote.cantidad_actual, 0)
        self.assertEqual(consumido, 5)

    def test_dos_ventas_simultaneas_no_sobrevenden(self):
        """
        Stock 5, dos cajas piden 4 cada una: sólo una puede completarse y el
        lote nunca queda con más unidades de las que le corresponden.
        """
        with patch(
            'apps.ventas.services.ventas_service._hook_imprimir_ticket'
        ), patch(
            'apps.sync.events.evento_inventario_snapshot'
        ):
            resultados = self._en_paralelo(lambda _: self._vender(4).numero_venta)

        exitos = [r for estado, r in resultados if estado == 'ok']
        errores = [r for estado, r in resultados if estado == 'error']

        self.assertEqual(len(exitos), 1, f'resultados={resultados}')
        self.assertEqual(errores, ['StockInsuficienteError'])

        lote = Lote.objects.get(producto=self.producto)
        self.assertEqual(lote.cantidad_actual, 1)

        # Invariante contable: lo descontado del lote == lo que dicen los movimientos.
        consumido = sum(
            abs(m.cantidad)
            for m in MovimientoLote.objects.filter(tipo='VENTA')
        )
        self.assertEqual(consumido, 4)
        self.assertEqual(Venta.objects.count(), 1)

    def test_dos_ventas_simultaneas_que_caben_se_aplican_ambas(self):
        """El lock serializa, no rechaza: si hay stock para ambas, ambas pasan."""
        with patch(
            'apps.ventas.services.ventas_service._hook_imprimir_ticket'
        ), patch(
            'apps.sync.events.evento_inventario_snapshot'
        ):
            resultados = self._en_paralelo(lambda _: self._vender(2).numero_venta)

        exitos = [r for estado, r in resultados if estado == 'ok']
        self.assertEqual(len(exitos), 2, f'resultados={resultados}')

        lote = Lote.objects.get(producto=self.producto)
        self.assertEqual(lote.cantidad_actual, 1)

    def test_numeracion_simultanea_no_colisiona(self):
        """
        VENTAS-002: dos cierres a la vez leían el mismo conteo y proponían el
        mismo `numero_venta`; uno moría con IntegrityError (500 con el cobro ya
        hecho). Ahora el reintento acotado le asigna el siguiente.
        """
        with patch(
            'apps.ventas.services.ventas_service._hook_imprimir_ticket'
        ), patch(
            'apps.sync.events.evento_inventario_snapshot'
        ):
            resultados = self._en_paralelo(lambda _: self._vender(1).numero_venta)

        numeros = sorted(r for estado, r in resultados if estado == 'ok')

        self.assertEqual(len(numeros), 2, f'resultados={resultados}')
        self.assertEqual(len(set(numeros)), 2, f'numeros repetidos: {numeros}')
        self.assertEqual(Venta.objects.count(), 2)


class AnulacionConcurrenteTests(ConcurrenciaTestCase):
    """VENTAS-008: dos anulaciones simultáneas no devuelven el stock dos veces."""

    def test_dos_anulaciones_simultaneas_solo_aplican_una(self):
        with patch(
            'apps.ventas.services.ventas_service._hook_imprimir_ticket'
        ), patch(
            'apps.sync.events.evento_inventario_snapshot'
        ):
            venta = self._vender(3)

            lote = Lote.objects.get(producto=self.producto)
            self.assertEqual(lote.cantidad_actual, 2)

            resultados = self._en_paralelo(
                lambda _: anular_venta_service(
                    usuario=self.admin,
                    venta_id=venta.id,
                    motivo='Anulacion concurrente de prueba',
                ).estado
            )

        exitos = [r for estado, r in resultados if estado == 'ok']
        errores = [r for estado, r in resultados if estado == 'error']

        self.assertEqual(exitos, ['ANULADA'], f'resultados={resultados}')
        self.assertEqual(errores, ['AnulacionNoPermitidaError'])

        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_actual, 5)
        self.assertEqual(
            MovimientoLote.objects.filter(tipo='ANULACION').count(), 1
        )
