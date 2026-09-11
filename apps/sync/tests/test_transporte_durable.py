"""Matriz A04: lease durable, reintento y alcance de idempotencia."""
import threading
import uuid
from datetime import timedelta
from unittest import mock

import requests
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.sucursales.models import Sucursal
from apps.sync.decorators import requiere_conexion_cloud
from apps.sync.engine import SyncEngine
from apps.sync.models import EventoSync


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


def _evento(sucursal, hash_payload='a' * 64, event_id=None):
    campos = {}
    if event_id is not None:
        campos['event_id'] = event_id
    return EventoSync.objects.create(
        sucursal=sucursal,
        tipo_evento='APERTURA_CAJA',
        objeto_referencia='Turno-1',
        payload={'turno_id_local': 1, 'sucursal_codigo': sucursal.codigo},
        hash_payload=hash_payload,
        **campos,
    )


class LeaseDurableTests(TestCase):
    def setUp(self):
        self.sucursal = Sucursal.objects.create(
            codigo='LEASE-01', nombre='Lease', activa=True,
        )

    def test_claim_persiste_lease_de_cinco_minutos_antes_del_http(self):
        evento = _evento(self.sucursal)
        antes = timezone.now()

        reclamados, lease_id = SyncEngine(lease_seconds=300)._reclamar_eventos()

        self.assertEqual([item.pk for item in reclamados], [evento.pk])
        self.assertIsNotNone(lease_id)
        evento.refresh_from_db()
        self.assertEqual(evento.estado, 'EN_VUELO')
        self.assertEqual(evento.lease_id, lease_id)
        self.assertGreaterEqual(
            evento.lease_expires_at,
            antes + timedelta(seconds=299),
        )
        self.assertEqual(SyncEngine()._reclamar_eventos(), ([], None))

    def test_worker_viejo_no_puede_confirmar_lease_reclamado_de_nuevo(self):
        evento = _evento(self.sucursal)
        primero, lease_viejo = SyncEngine(lease_seconds=300)._reclamar_eventos()
        EventoSync.objects.filter(pk=evento.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )

        segundo, lease_nuevo = SyncEngine(lease_seconds=300)._reclamar_eventos()

        self.assertEqual(primero[0].pk, segundo[0].pk)
        self.assertNotEqual(lease_viejo, lease_nuevo)
        self.assertFalse(primero[0].marcar_confirmado(lease_id=lease_viejo))
        self.assertTrue(segundo[0].marcar_confirmado(lease_id=lease_nuevo))
        evento.refresh_from_db()
        self.assertEqual(evento.estado, 'CONFIRMADO')

    @mock.patch('apps.sync.engine.requests.post')
    def test_ack_perdido_reintenta_misma_identidad_y_acepta_duplicado(self, post):
        evento = _evento(self.sucursal)
        post.side_effect = requests.ConnectionError('ACK perdido')
        engine = SyncEngine(cloud_url='https://cloud.test', token='t')

        primero = engine.push_eventos()

        evento.refresh_from_db()
        self.assertEqual(primero, {
            'procesados': 1, 'confirmados': 0, 'fallidos': 1,
        })
        self.assertEqual(evento.estado, 'ERROR')
        self.assertEqual(evento.intentos, 1)

        post.side_effect = None
        post.return_value = _Resp({
            'recibidos': 0,
            'duplicados': 1,
            'errores': 0,
            'detalle': [{
                'event_id': str(evento.event_id),
                'hash': evento.hash_payload,
                'estado': 'DUPLICADO',
            }],
        })
        segundo = engine.push_eventos()

        self.assertEqual(segundo, {
            'procesados': 1, 'confirmados': 1, 'fallidos': 0,
        })
        evento.refresh_from_db()
        self.assertEqual(evento.estado, 'CONFIRMADO')
        self.assertIsNone(evento.lease_id)


class ClaimConcurrenteTests(TransactionTestCase):
    def setUp(self):
        self.sucursal = Sucursal.objects.create(
            codigo='LEASE-CONC', nombre='Lease concurrente', activa=True,
        )
        self.evento = _evento(self.sucursal)

    def tearDown(self):
        connection.close()

    def test_dos_procesos_no_reclaman_el_mismo_evento(self):
        barrera = threading.Barrier(2, timeout=30)
        resultados = []
        errores = []
        candado = threading.Lock()

        def reclamar():
            close_old_connections()
            try:
                barrera.wait()
                eventos, lease_id = SyncEngine()._reclamar_eventos()
                with candado:
                    resultados.append(([item.pk for item in eventos], lease_id))
            except Exception as exc:  # pragma: no cover - se reporta abajo
                with candado:
                    errores.append(exc)
            finally:
                close_old_connections()

        hilos = [threading.Thread(target=reclamar) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=30)

        self.assertEqual(errores, [])
        self.assertEqual(len(resultados), 2)
        reclamados = [fila for fila in resultados if fila[0]]
        vacios = [fila for fila in resultados if not fila[0]]
        self.assertEqual(len(reclamados), 1)
        self.assertEqual(len(vacios), 1)
        self.assertEqual(reclamados[0][0], [self.evento.pk])


class AlcanceIdempotenciaTests(TestCase):
    def setUp(self):
        self.sucursal_a = Sucursal.objects.create(
            codigo='IDEM-A', nombre='Idem A', activa=True,
        )
        self.sucursal_b = Sucursal.objects.create(
            codigo='IDEM-B', nombre='Idem B', activa=True,
        )

    def test_hash_y_event_id_son_unicos_dentro_de_sucursal_no_globales(self):
        event_id = uuid.uuid4()
        _evento(self.sucursal_a, hash_payload='b' * 64, event_id=event_id)
        _evento(self.sucursal_b, hash_payload='b' * 64, event_id=event_id)

        with self.assertRaises(IntegrityError), transaction.atomic():
            _evento(self.sucursal_a, hash_payload='b' * 64)

        with self.assertRaises(IntegrityError), transaction.atomic():
            _evento(self.sucursal_a, hash_payload='c' * 64, event_id=event_id)


class ConectividadConfigurableTests(TestCase):
    @mock.patch('apps.sync.engine.time.sleep')
    @mock.patch('apps.sync.engine.requests.get')
    def test_health_reintenta_con_timeout_configurable(self, get, sleep):
        get.side_effect = [
            requests.ConnectionError('cold start'),
            _Resp({}, status_code=503),
            _Resp({}, status_code=200),
        ]
        engine = SyncEngine(
            cloud_url='https://cloud.test',
            health_timeout=7,
            health_retries=3,
            health_backoff=0.25,
        )

        self.assertTrue(engine.check_connection())
        self.assertEqual(get.call_count, 3)
        self.assertTrue(all(
            llamada.kwargs['timeout'] == 7 for llamada in get.call_args_list
        ))
        self.assertEqual(sleep.call_args_list, [mock.call(0.25), mock.call(0.25)])

    @mock.patch('apps.sync.engine.SyncEngine.check_connection')
    def test_editar_local_no_exige_ping_previo(self, check_connection):
        @requiere_conexion_cloud(redirect_url='productos:lista')
        def editar(request, producto_id):
            return producto_id

        self.assertEqual(editar(object(), 42), 42)
        check_connection.assert_not_called()
