"""Contrato A04 del receptor y la sonda read-only para BUG-K."""
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.sucursales.models import Sucursal
from apps.sync.models import EventoSync, InventarioSucursalSnapshot


User = get_user_model()


class ReceptorScopeTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(
            'svc_a04_a', 'a04a@test.local', 'x', rol='CAJERA',
        )
        self.user_b = User.objects.create_user(
            'svc_a04_b', 'a04b@test.local', 'x', rol='CAJERA',
        )
        self.sucursal_a = Sucursal.objects.create(
            codigo='A04-A', nombre='A04 A', activa=True,
            usuario_servicio=self.user_a,
        )
        self.sucursal_b = Sucursal.objects.create(
            codigo='A04-B', nombre='A04 B', activa=True,
            usuario_servicio=self.user_b,
        )
        self.token_a = Token.objects.create(user=self.user_a)
        self.token_b = Token.objects.create(user=self.user_b)

    @staticmethod
    def _api(token):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        return client

    @staticmethod
    def _evento(event_id, hash_payload, payload=None):
        return {
            'event_id': str(event_id),
            'tipo_evento': 'APERTURA_CAJA',
            'payload': payload or {'turno_id_local': 11},
            'hash_payload': hash_payload,
            'timestamp': timezone.now().isoformat(),
        }

    @patch.dict(
        'apps.api.views.sync.HANDLERS',
        {'APERTURA_CAJA': lambda sucursal, payload: None},
    )
    def test_misma_identidad_y_hash_pueden_existir_en_sucursales_distintas(self):
        event_id = uuid.uuid4()
        evento = self._evento(event_id, 'scope-hash')

        respuesta_a = self._api(self.token_a).post(
            '/api/v1/sync/eventos/', {'eventos': [evento]}, format='json',
        )
        respuesta_b = self._api(self.token_b).post(
            '/api/v1/sync/eventos/', {'eventos': [evento]}, format='json',
        )

        self.assertEqual(respuesta_a.data['detalle'][0]['estado'], 'CONFIRMADO')
        self.assertEqual(respuesta_b.data['detalle'][0]['estado'], 'CONFIRMADO')
        self.assertEqual(EventoSync.objects.filter(event_id=event_id).count(), 2)

    @patch.dict(
        'apps.api.views.sync.HANDLERS',
        {'APERTURA_CAJA': lambda sucursal, payload: None},
    )
    def test_misma_identidad_con_otro_contenido_es_error_real(self):
        event_id = uuid.uuid4()
        primero = self._evento(event_id, 'hash-original')
        segundo = self._evento(
            event_id,
            'hash-distinto',
            payload={'turno_id_local': 12},
        )
        api = self._api(self.token_a)

        self.assertEqual(
            api.post(
                '/api/v1/sync/eventos/', {'eventos': [primero]}, format='json',
            ).data['detalle'][0]['estado'],
            'CONFIRMADO',
        )
        respuesta = api.post(
            '/api/v1/sync/eventos/', {'eventos': [segundo]}, format='json',
        )

        detalle = respuesta.data['detalle'][0]
        self.assertEqual(detalle['estado'], 'ERROR')
        self.assertEqual(detalle['codigo'], 'EVENT_IDENTITY_CONFLICT')

    @patch.dict(
        'apps.api.views.sync.HANDLERS',
        {'APERTURA_CAJA': lambda sucursal, payload: None},
    )
    def test_payload_que_declara_otra_sucursal_se_rechaza(self):
        evento = self._evento(
            uuid.uuid4(),
            'scope-mismatch',
            payload={'turno_id_local': 13, 'sucursal_codigo': 'A04-B'},
        )

        respuesta = self._api(self.token_a).post(
            '/api/v1/sync/eventos/', {'eventos': [evento]}, format='json',
        )

        detalle = respuesta.data['detalle'][0]
        self.assertEqual(detalle['estado'], 'ERROR')
        self.assertEqual(detalle['codigo'], 'EVENT_SCOPE_MISMATCH')
        self.assertFalse(EventoSync.objects.filter(hash_payload='scope-mismatch').exists())


class ReconciliacionBugKTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(
            'svc_sonda_a', 'sondaa@test.local', 'x', rol='CAJERA',
        )
        self.user_b = User.objects.create_user(
            'svc_sonda_b', 'sondab@test.local', 'x', rol='CAJERA',
        )
        self.sucursal_a = Sucursal.objects.create(
            codigo='SONDA-A', nombre='Sonda A', activa=True,
            usuario_servicio=self.user_a,
        )
        self.sucursal_b = Sucursal.objects.create(
            codigo='SONDA-B', nombre='Sonda B', activa=True,
            usuario_servicio=self.user_b,
        )
        self.token_a = Token.objects.create(user=self.user_a)

    def _api(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {self.token_a.key}')
        return client

    @staticmethod
    def _sonda(event_id, tipo, payload, hash_payload, referencia=''):
        return {
            'event_id': str(event_id),
            'tipo_evento': tipo,
            'payload': payload,
            'hash_payload': hash_payload,
            'objeto_referencia': referencia,
        }

    def test_clasifica_ledger_divergencia_hecho_falso_ack_y_ambito(self):
        payload_ledger = {'turno_id_local': 21}
        id_ledger = uuid.uuid4()
        EventoSync.objects.create(
            sucursal=self.sucursal_a,
            event_id=id_ledger,
            tipo_evento='APERTURA_CAJA',
            payload=payload_ledger,
            hash_payload='ledger-real',
            objeto_referencia='Turno-21',
            estado='CONFIRMADO',
        )

        fecha_snapshot = timezone.now()
        InventarioSucursalSnapshot.objects.create(
            sucursal=self.sucursal_a,
            producto_sku='A04-SKU',
            timestamp=fecha_snapshot,
        )

        id_otro_scope = uuid.uuid4()
        EventoSync.objects.create(
            sucursal=self.sucursal_b,
            event_id=id_otro_scope,
            tipo_evento='VENTA_CREADA',
            payload={'numero_venta': 'V-OTRA-RAMA'},
            hash_payload='solo-otra-sucursal',
            objeto_referencia='V-OTRA-RAMA',
            estado='CONFIRMADO',
        )

        eventos = [
            self._sonda(
                id_ledger, 'APERTURA_CAJA', payload_ledger, 'ledger-real',
                'Turno-21',
            ),
            self._sonda(
                id_ledger,
                'APERTURA_CAJA',
                {'turno_id_local': 22},
                'ledger-divergente',
                'Turno-22',
            ),
            self._sonda(
                uuid.uuid4(),
                'INVENTARIO_SNAPSHOT',
                {
                    'timestamp': fecha_snapshot.isoformat(),
                    'sucursal_codigo': self.sucursal_a.codigo,
                },
                'hecho-sin-ledger',
            ),
            self._sonda(
                uuid.uuid4(),
                'VENTA_CREADA',
                {
                    'numero_venta': 'V-NO-EXISTE',
                    'sucursal_codigo': self.sucursal_a.codigo,
                },
                'falso-ack',
                'V-NO-EXISTE',
            ),
            self._sonda(
                id_otro_scope,
                'VENTA_CREADA',
                {'numero_venta': 'V-OTRA-RAMA'},
                'solo-otra-sucursal',
                'V-OTRA-RAMA',
            ),
            self._sonda(
                uuid.uuid4(),
                'VENTA_CREADA',
                {
                    'numero_venta': 'V-SCOPE-MALO',
                    'sucursal_codigo': self.sucursal_b.codigo,
                },
                'ambito-invalido',
                'V-SCOPE-MALO',
            ),
        ]

        respuesta = self._api().post(
            '/api/v1/sync/reconciliacion-eventos/',
            {'schema_version': 'sync.reconciliation.v1', 'eventos': eventos},
            format='json',
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data['schema_version'], 'sync.reconciliation.v1')
        self.assertEqual(respuesta.data['scope']['branch_code'], 'SONDA-A')
        por_hash = {
            item['hash']: item['clasificacion']
            for item in respuesta.data['resultados']
        }
        self.assertEqual(por_hash, {
            'ledger-real': 'DUPLICADO_REAL',
            'ledger-divergente': 'DIVERGENCIA_CONTENIDO',
            'hecho-sin-ledger': 'HECHO_SIN_EVENTO_CLOUD',
            'falso-ack': 'FALSO_ACK',
            'solo-otra-sucursal': 'FALSO_ACK',
            'ambito-invalido': 'AMBITO_INVALIDO',
        })

        # La sonda es estrictamente read-only.
        self.assertEqual(EventoSync.objects.count(), 2)
        self.assertEqual(InventarioSucursalSnapshot.objects.count(), 1)
