"""
Tests del outbox transaccional (Fase 1, BUG-A).

Lo que se garantiza aqui:

1. El evento se escribe AUNQUE el sync este apagado (antes se perdia).
2. El evento vive y muere con la transaccion de negocio.
3. Un serializador roto NO tumba la venta: degrada a SIN_PAYLOAD.
4. El push re-serializa los SIN_PAYLOAD antes de enviarlos.
5. El snapshot de inventario conserva su gate a proposito.
"""
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, TransactionTestCase, override_settings

from apps.clientes.models import Cliente
from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal
from apps.sync import events as sync_events
from apps.sync.models import EventoSync
from apps.ventas.models import Venta

User = get_user_model()


class OutboxTestsBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            'cajera_outbox', 'cajera_outbox@test.local', 'x', rol='CAJERA'
        )
        self.sucursal = Sucursal.objects.create(
            codigo='SD-001', nombre='Sucursal SD', activa=True,
            usuario_servicio=self.user,
        )
        self.categoria = Categoria.objects.create(nombre='Plasticos')
        self.producto = Producto.objects.create(
            sku='SKU-OB-1', nombre='Vaso', categoria=self.categoria,
            precio_venta=Decimal('25.00'), stock_minimo=5,
        )

    def _venta(self, numero='V-20260819-0001'):
        return Venta.objects.create(
            numero_venta=numero,
            sucursal=self.sucursal,
            usuario=self.user,
            subtotal=Decimal('100.00'),
            total=Decimal('100.00'),
            estado='COMPLETADA',
        )


class GateDeSyncEnabledTests(OutboxTestsBase):
    @override_settings(SYNC_ENABLED=False)
    def test_evento_se_encola_aunque_el_sync_este_apagado(self):
        """
        El corazon de BUG-A. Antes, `SYNC_ENABLED=False` hacia que la venta se
        guardara sin encolar nada: no habia fila que reintentar y el dato se
        perdia para siempre. Ahora el gate vive en el ENVIO, no en la emision.
        """
        venta = self._venta()

        evento = sync_events.evento_venta_creada(venta)

        self.assertIsNotNone(evento, 'El evento no se encolo con SYNC_ENABLED=False')
        self.assertEqual(evento.estado, 'PENDIENTE')
        self.assertEqual(evento.objeto_id_local, venta.pk)
        self.assertTrue(EventoSync.objects.filter(tipo_evento='VENTA_CREADA').exists())

    @override_settings(SYNC_ENABLED=False)
    def test_snapshot_de_inventario_si_respeta_el_gate(self):
        """
        Excepcion deliberada: el snapshot recorre todo el catalogo calculando
        FIFO y guarda el inventario completo en el payload. Acumular eso sin
        cloud llenaria la BD local de JSON inutil. Ademas es una foto de estado:
        perder una es inocuo porque la siguiente la reemplaza.
        """
        evento = sync_events.evento_inventario_snapshot(sucursal=self.sucursal)

        self.assertIsNone(evento)
        self.assertFalse(EventoSync.objects.filter(tipo_evento='INVENTARIO_SNAPSHOT').exists())

    @override_settings(SYNC_ENABLED=True)
    def test_snapshot_se_encola_cuando_hay_cloud(self):
        evento = sync_events.evento_inventario_snapshot(sucursal=self.sucursal)

        self.assertIsNotNone(evento)
        self.assertEqual(evento.tipo_evento, 'INVENTARIO_SNAPSHOT')


class AtomicidadTests(TransactionTestCase):
    """
    Necesita TransactionTestCase: `TestCase` envuelve cada test en una
    transaccion que nunca commitea, asi que no se puede observar un rollback
    real.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            'cajera_atomic', 'cajera_atomic@test.local', 'x', rol='CAJERA'
        )
        self.sucursal = Sucursal.objects.create(
            codigo='SD-002', nombre='Sucursal Atomic', activa=True,
            usuario_servicio=self.user,
        )

    @override_settings(SYNC_ENABLED=False)
    def test_rollback_de_la_venta_se_lleva_el_evento(self):
        """La otra mitad del outbox: nunca un evento de algo que no ocurrio."""
        class FalloDeNegocio(Exception):
            pass

        with self.assertRaises(FalloDeNegocio):
            with transaction.atomic():
                venta = Venta.objects.create(
                    numero_venta='V-20260819-9999',
                    sucursal=self.sucursal,
                    usuario=self.user,
                    subtotal=Decimal('10.00'),
                    total=Decimal('10.00'),
                    estado='COMPLETADA',
                )
                sync_events.evento_venta_creada(venta)
                raise FalloDeNegocio('algo salio mal despues de encolar')

        self.assertFalse(Venta.objects.filter(numero_venta='V-20260819-9999').exists())
        self.assertFalse(
            EventoSync.objects.filter(objeto_referencia='V-20260819-9999').exists(),
            'Quedo un evento huerfano de una venta que hizo rollback.',
        )

    @override_settings(SYNC_ENABLED=False)
    def test_commit_conserva_venta_y_evento_juntos(self):
        with transaction.atomic():
            venta = Venta.objects.create(
                numero_venta='V-20260819-8888',
                sucursal=self.sucursal,
                usuario=self.user,
                subtotal=Decimal('10.00'),
                total=Decimal('10.00'),
                estado='COMPLETADA',
            )
            sync_events.evento_venta_creada(venta)

        self.assertTrue(Venta.objects.filter(numero_venta='V-20260819-8888').exists())
        self.assertTrue(EventoSync.objects.filter(objeto_referencia='V-20260819-8888').exists())


class SerializacionDegradadaTests(OutboxTestsBase):
    @override_settings(SYNC_ENABLED=False)
    def test_serializador_roto_no_tumba_la_operacion(self):
        """
        Un POS no puede perder una venta porque falle un serializador. El evento
        se encola igual, sin payload, y el push lo reintenta.
        """
        venta = self._venta('V-20260819-0002')

        with mock.patch('apps.sync.serializers.serializar_venta',
                        side_effect=RuntimeError('boom')):
            evento = sync_events.evento_venta_creada(venta)

        self.assertIsNotNone(evento)
        self.assertEqual(evento.estado, 'SIN_PAYLOAD')
        self.assertIsNone(evento.payload)
        self.assertEqual(evento.hash_payload, '')
        self.assertEqual(evento.objeto_id_local, venta.pk)

    @override_settings(SYNC_ENABLED=True, CLOUD_API_URL='https://cloud.test',
                       CLOUD_API_TOKEN='t')
    def test_push_reserializa_los_eventos_sin_payload(self):
        from apps.sync.engine import SyncEngine

        venta = self._venta('V-20260819-0003')
        with mock.patch('apps.sync.serializers.serializar_venta',
                        side_effect=RuntimeError('boom')):
            evento = sync_events.evento_venta_creada(venta)

        enviables = SyncEngine()._completar_payloads([evento])

        self.assertEqual(len(enviables), 1)
        evento.refresh_from_db()
        self.assertEqual(evento.estado, 'PENDIENTE')
        self.assertIsNotNone(evento.payload)
        self.assertEqual(evento.payload['numero_venta'], 'V-20260819-0003')
        self.assertEqual(len(evento.hash_payload), 64)

    @override_settings(SYNC_ENABLED=True, CLOUD_API_URL='https://cloud.test',
                       CLOUD_API_TOKEN='t')
    def test_evento_sin_payload_cuyo_objeto_desaparecio_se_marca_error(self):
        from apps.sync.engine import SyncEngine

        evento = EventoSync.objects.create(
            sucursal=self.sucursal,
            tipo_evento='VENTA_CREADA',
            objeto_id_local=999999,
            payload=None,
            hash_payload='',
            estado='SIN_PAYLOAD',
        )

        enviables = SyncEngine()._completar_payloads([evento])

        self.assertEqual(enviables, [])
        evento.refresh_from_db()
        self.assertIn(evento.estado, ('ERROR', 'DESCARTADO'))
        self.assertIn('ya no existe', evento.ultimo_error)


class OrdenDeEventosTests(OutboxTestsBase):
    @override_settings(SYNC_ENABLED=False)
    def test_venta_se_encola_antes_que_su_cuenta_por_cobrar(self):
        """
        El handler cloud de CXC_CREADA rechaza la cuenta si su venta todavia no
        llego, y los eventos se empujan en orden de creacion. Con `on_commit` el
        orden salia invertido porque `crear_cuenta_para_venta` registraba su
        callback primero.
        """
        venta = self._venta('V-20260819-0004')
        cliente = Cliente.objects.create(tipo='PERSONAL', nombre='Deudor')

        sync_events.evento_venta_creada(venta)
        # Simula lo que hace crear_cuenta_para_venta despues de la venta.
        EventoSync.objects.create(
            sucursal=self.sucursal,
            tipo_evento='CXC_CREADA',
            objeto_referencia=venta.numero_venta,
            payload={'numero_venta': venta.numero_venta},
            hash_payload='hash-cxc',
            estado='PENDIENTE',
        )

        eventos = list(
            EventoSync.objects.filter(objeto_referencia=venta.numero_venta)
            .order_by('created_at', 'id')
            .values_list('tipo_evento', flat=True)
        )

        self.assertEqual(eventos, ['VENTA_CREADA', 'CXC_CREADA'])
        self.assertNotEqual(cliente.pk, None)
