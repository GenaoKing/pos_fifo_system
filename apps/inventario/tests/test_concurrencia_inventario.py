"""
apps/inventario/tests/test_concurrencia_inventario.py

Carreras reales sobre el mismo lote. Necesitan `TransactionTestCase` y dos
conexiones: un `TestCase` envuelve todo en una transaccion que nunca commitea,
asi que los hilos no se ven entre si.

Hallazgos: INVENTARIO-005 (dos ajustes simultaneos) e INVENTARIO-006 (corregir
una compra mientras una venta consume su lote).

La asercion central es contable, no de resultado:

    suma(movimientos del lote) == lote.cantidad_actual

Un lost update rompe esa igualdad aunque el saldo final "parezca" razonable.
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
from apps.inventario.services import registrar_ajuste_service
from apps.productos.models import Categoria, Producto

User = get_user_model()


class ConcurrenciaInventarioTests(TransactionTestCase):
    # Sin `serialized_rollback`: choca con django_content_type al
    # redeserializar. El runner de Django corre estos casos despues de los
    # TestCase normales. Ver apps/ventas/tests/test_concurrencia.py.

    def setUp(self):
        cache.clear()

        self.admin = User.objects.create_user(
            username='admin_conc_inv', email='admin_conc_inv@test.local',
            password='pass', rol='ADMIN', activo=True,
        )
        self.categoria = Categoria.objects.create(nombre='Concurrencia Inv')
        self.producto = Producto.objects.create(
            sku='CONC-INV-001', codigo_barras='CONC-INV-001',
            nombre='Producto concurrencia inventario', descripcion='',
            categoria=self.categoria, precio_venta=Decimal('100.00'),
            stock_minimo=1, activo=True, estado='nuevo', marca='', atributos={},
        )
        self.compra = Compra.objects.create(
            usuario=self.admin, proveedor='Proveedor Conc',
            numero_factura='FAC-CONC-INV', total=Decimal('40.00'),
        )
        self.detalle = DetalleCompra.objects.create(
            compra=self.compra, producto=self.producto,
            cantidad=10, costo_unitario=Decimal('4.00'), subtotal=Decimal('40.00'),
        )
        self.lote = self.detalle.lote

        # Materializa la config antes de lanzar hilos (su singleton tiene su
        # propia carrera de creacion, ajena a lo que se prueba aca).
        get_config()

    def tearDown(self):
        cache.clear()

    # -- helpers ------------------------------------------------------------

    def _en_paralelo(self, objetivo, veces=2, timeout=30):
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

        self.assertEqual(len(resultados), veces, 'algun hilo no termino')
        return resultados

    def _asertar_ledger_cuadra(self):
        self.lote.refresh_from_db()
        suma = sum(
            m.cantidad for m in MovimientoLote.objects.filter(lote=self.lote)
        )
        self.assertEqual(
            suma, self.lote.cantidad_actual,
            f'ledger={suma} vs lote={self.lote.cantidad_actual} '
            f'({list(MovimientoLote.objects.filter(lote=self.lote).values_list("tipo", "cantidad"))})',
        )


class AjustesConcurrentesTests(ConcurrenciaInventarioTests):
    """INVENTARIO-005: la validacion de suficiencia corria FUERA del lock."""

    def test_dos_ajustes_simultaneos_no_sobregiran_el_lote(self):
        """
        Lote con 10; dos usuarios retiran 8 cada uno. Antes ambos leian 10,
        ambos pasaban la validacion, y cada uno escribia 2 desde su copia: el
        saldo final no representaba los -16 del ledger.
        """
        with patch('apps.sync.events.evento_inventario_snapshot'):
            resultados = self._en_paralelo(
                lambda i: registrar_ajuste_service(
                    usuario=self.admin, lote_id=self.lote.id, tipo='MERMA',
                    cantidad=8, motivo=f'Merma concurrente {i}',
                ).id
            )

        exitos = [r for estado, r in resultados if estado == 'ok']
        errores = [r for estado, r in resultados if estado == 'error']

        self.assertEqual(len(exitos), 1, f'resultados={resultados}')
        self.assertEqual(errores, ['StockInsuficienteLoteError'])

        self.lote.refresh_from_db()
        self.assertEqual(self.lote.cantidad_actual, 2)
        self._asertar_ledger_cuadra()

    def test_dos_ajustes_que_caben_se_aplican_ambos(self):
        """El lock serializa, no rechaza: si hay stock para ambos, ambos pasan."""
        with patch('apps.sync.events.evento_inventario_snapshot'):
            resultados = self._en_paralelo(
                lambda i: registrar_ajuste_service(
                    usuario=self.admin, lote_id=self.lote.id, tipo='MERMA',
                    cantidad=3, motivo=f'Merma concurrente {i}',
                ).id
            )

        exitos = [r for estado, r in resultados if estado == 'ok']
        self.assertEqual(len(exitos), 2, f'resultados={resultados}')

        self.lote.refresh_from_db()
        self.assertEqual(self.lote.cantidad_actual, 4)
        self._asertar_ledger_cuadra()

    def test_el_lock_es_lo_que_sostiene_la_invariante(self):
        """
        Prueba dirigida: se ensancha la ventana entre lectura y escritura.

        Sin `select_for_update` ambos hilos leen 10, ambos escriben 7 y el
        ledger dice -6: la invariante se rompe.
        """
        original_save = Lote.save

        def save_lento(self, *args, **kwargs):
            time.sleep(0.4)
            return original_save(self, *args, **kwargs)

        with patch('apps.sync.events.evento_inventario_snapshot'), \
                patch.object(Lote, 'save', save_lento):
            self._en_paralelo(
                lambda i: registrar_ajuste_service(
                    usuario=self.admin, lote_id=self.lote.id, tipo='MERMA',
                    cantidad=3, motivo=f'Merma lenta {i}',
                ).id
            )

        self._asertar_ledger_cuadra()
        self.lote.refresh_from_db()
        self.assertEqual(self.lote.cantidad_actual, 4)


class CompraEditarVsVentaTests(ConcurrenciaInventarioTests):
    """INVENTARIO-006: la correccion de compra no participaba de los locks."""

    def test_corregir_la_compra_no_restaura_stock_que_una_venta_consumio(self):
        """
        La correccion lee el lote intacto (10). Una venta consume 2. Antes la
        correccion seguia con su instancia vieja y escribia `cantidad_actual`
        de nuevo, devolviendo el lote a 10: stock ya entregado, revendible.

        Ahora la correccion bloquea el lote antes de leerlo, asi que las dos
        operaciones se serializan y el ledger cuadra pase lo que pase.
        """
        from apps.inventario.fifo_logic import procesar_venta_fifo

        original_save = Lote.save

        def save_lento(self, *args, **kwargs):
            time.sleep(0.4)
            return original_save(self, *args, **kwargs)

        def corregir(_):
            from django.test import Client

            cliente = Client()
            cliente.force_login(self.admin)
            import json
            from django.urls import reverse

            return cliente.post(
                reverse('inventario:compra_editar', args=[self.compra.id]),
                data=json.dumps({
                    'proveedor': 'Proveedor Corregido',
                    'numero_factura': 'FAC-CONC-INV',
                    'notas': '',
                    'lineas': [{
                        'detalle_id': self.detalle.id,
                        'producto_id': self.producto.id,
                        'cantidad': 10,
                        'costo_unitario': 9.00,   # solo cambia el costo
                        'eliminar': False,
                    }],
                }),
                content_type='application/json',
            ).status_code

        def vender(_):
            return procesar_venta_fifo(
                producto_id=self.producto.id, cantidad_solicitada=2,
                venta_id=7777, usuario=self.admin,
            )['cantidad_vendida']

        acciones = [corregir, vender]

        with patch('apps.sync.events.evento_inventario_snapshot'), \
                patch.object(Lote, 'save', save_lento):
            self._en_paralelo(lambda i: acciones[i](i))

        # La venta consumio 2 y la correccion NO las devolvio.
        self.lote.refresh_from_db()
        self.assertEqual(self.lote.cantidad_actual, 8)
        self._asertar_ledger_cuadra()

        # Y la correccion de costo si se aplico.
        self.assertEqual(self.lote.costo_unitario, Decimal('9.00'))
