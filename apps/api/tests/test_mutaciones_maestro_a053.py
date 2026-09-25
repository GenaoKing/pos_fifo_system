"""Contrato interno A05.3: receptor cloud de propuestas de maestros."""
import threading
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.auditoria.models import Auditoria
from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal
from apps.sync.models import MutacionMaestro


User = get_user_model()


class _RecepcionMutacionesMaestroBase:
    def setUp(self):
        self.servicio = User.objects.create_user(
            'svc_a053', 'svc-a053@example.test', 'x', rol='CAJERA',
        )
        self.actor = User.objects.create_user(
            'operador_a053', 'operador-a053@example.test', 'x', rol='ADMIN',
        )
        self.sucursal = Sucursal.objects.create(
            codigo='A053-01', nombre='Sucursal A053', activa=True,
            usuario_servicio=self.servicio,
        )
        self.token = Token.objects.create(user=self.servicio)
        self.categoria = Categoria.objects.create(nombre='Categoría cloud A053')

    def _api(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        return client

    def _enviar(self, propuesta):
        return self._api().post(
            '/api/v1/sync/mutaciones-maestro/',
            {'schema_version': 'master.mutation.v1', 'mutaciones': [propuesta]},
            format='json',
        )

    def _crear_producto(self, *, mutacion_id=None, actor_username=None, sku='A053-SKU'):
        return {
            'schema_version': 'master.mutation.v1',
            'mutacion_id': str(mutacion_id or uuid.uuid4()),
            'entidad': MutacionMaestro.Entidad.PRODUCTO,
            'entidad_local_id': 501,
            'operacion': MutacionMaestro.Operacion.CREAR,
            'revision_base': '',
            'actor_username': actor_username or self.actor.username,
            'categoria_cloud_id': self.categoria.pk,
            'delta': {
                'id': {'before': None, 'after': 501},
                'sku': {'before': None, 'after': sku},
                'nombre': {'before': None, 'after': 'Producto A053'},
                'descripcion': {'before': None, 'after': ''},
                'categoria_id': {'before': None, 'after': 21},
                'precio_venta': {'before': None, 'after': '125.50'},
                'stock_minimo': {'before': None, 'after': 2},
                'activo': {'before': None, 'after': True},
                'atributos': {'before': None, 'after': {}},
                'estado': {'before': None, 'after': 'nuevo'},
                'marca': {'before': None, 'after': ''},
            },
        }


class RecepcionMutacionesMaestroA053Tests(
    _RecepcionMutacionesMaestroBase,
    TestCase,
):
    """Casos funcionales del receptor cloud A05.3."""

    def test_creacion_confirma_audita_y_replay_no_duplica(self):
        propuesta = self._crear_producto()

        primera = self._enviar(propuesta)

        self.assertEqual(primera.status_code, 200)
        self.assertEqual(primera.data['schema_version'], 'master.mutation.v1')
        detalle = primera.data['detalle'][0]
        self.assertEqual(detalle['estado'], 'CONFIRMADA')
        self.assertIn('cloud_entidad_id', detalle)
        self.assertIn('cloud_revision', detalle)
        self.assertEqual(Producto.objects.filter(sku='A053-SKU').count(), 1)
        ledger = MutacionMaestro.objects.get(mutacion_id=propuesta['mutacion_id'])
        self.assertEqual(ledger.estado, MutacionMaestro.Estado.CONFIRMADA)
        self.assertEqual(ledger.cloud_entidad_id, detalle['cloud_entidad_id'])
        self.assertTrue(ledger.cloud_revision)
        self.assertTrue(Auditoria.objects.filter(
            accion='sync.maestro.aplicado',
            correlacion_id=propuesta['mutacion_id'],
            resultado=Auditoria.Resultado.SUCCEEDED,
        ).exists())

        replay = self._enviar(propuesta)

        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data['schema_version'], 'master.mutation.v1')
        self.assertEqual(replay.data['detalle'][0]['estado'], 'DUPLICADA')
        self.assertEqual(Producto.objects.filter(sku='A053-SKU').count(), 1)
        self.assertEqual(MutacionMaestro.objects.count(), 1)

    def test_cas_desactualizado_preserva_propuesta_y_no_pisa_cloud(self):
        producto = Producto.objects.create(
            sku='A053-CAS', codigo_barras='A053-CAS', nombre='Cloud vigente',
            descripcion='', categoria=self.categoria,
            precio_venta=Decimal('10.00'), stock_minimo=1, activo=True,
            atributos={}, estado='nuevo', marca='',
        )
        propuesta = {
            'schema_version': 'master.mutation.v1',
            'mutacion_id': str(uuid.uuid4()),
            'entidad': MutacionMaestro.Entidad.PRODUCTO,
            'entidad_local_id': 502,
            'operacion': MutacionMaestro.Operacion.ACTUALIZAR,
            'revision_base': '2000-01-01T00:00:00+00:00',
            'actor_username': self.actor.username,
            'cloud_entidad_id': producto.pk,
            'delta': {
                'nombre': {'before': 'Local viejo', 'after': 'No debe aplicar'},
            },
        }

        respuesta = self._enviar(propuesta)

        self.assertEqual(respuesta.status_code, 200)
        detalle = respuesta.data['detalle'][0]
        self.assertEqual(detalle['estado'], 'CONFLICTO')
        self.assertEqual(detalle['codigo'], 'MASTER_REVISION_CONFLICT')
        producto.refresh_from_db()
        self.assertEqual(producto.nombre, 'Cloud vigente')
        ledger = MutacionMaestro.objects.get(mutacion_id=propuesta['mutacion_id'])
        self.assertEqual(ledger.estado, MutacionMaestro.Estado.CONFLICTO)
        self.assertEqual(ledger.cloud_entidad_id, producto.pk)
        self.assertTrue(Auditoria.objects.filter(
            accion='sync.maestro.conflicto',
            correlacion_id=propuesta['mutacion_id'],
            resultado=Auditoria.Resultado.FAILED,
        ).exists())

    def test_revocacion_actual_rechaza_y_no_finge_confirmacion(self):
        cajera = User.objects.create_user(
            'cajera_a053', 'cajera-a053@example.test', 'x', rol='CAJERA',
        )
        propuesta = self._crear_producto(
            actor_username=cajera.username, sku='A053-REVOKED',
        )

        respuesta = self._enviar(propuesta)

        self.assertEqual(respuesta.status_code, 200)
        detalle = respuesta.data['detalle'][0]
        self.assertEqual(detalle['estado'], 'RECHAZADA')
        self.assertEqual(detalle['codigo'], 'MASTER_PERMISSION_REVOKED')
        self.assertFalse(Producto.objects.filter(sku='A053-REVOKED').exists())
        ledger = MutacionMaestro.objects.get(mutacion_id=propuesta['mutacion_id'])
        self.assertEqual(ledger.estado, MutacionMaestro.Estado.RECHAZADA)
        self.assertTrue(Auditoria.objects.filter(
            accion='sync.maestro.rechazado',
            correlacion_id=propuesta['mutacion_id'],
            resultado=Auditoria.Resultado.DENIED,
        ).exists())

    def test_mismo_uuid_con_otro_contenido_es_conflicto_y_no_crea_otra_fila(self):
        identificador = uuid.uuid4()
        primera = self._crear_producto(mutacion_id=identificador, sku='A053-ID-1')
        segunda = self._crear_producto(mutacion_id=identificador, sku='A053-ID-2')

        self.assertEqual(self._enviar(primera).data['detalle'][0]['estado'], 'CONFIRMADA')
        respuesta = self._enviar(segunda)

        self.assertEqual(respuesta.data['detalle'][0]['estado'], 'CONFLICTO')
        self.assertEqual(
            respuesta.data['detalle'][0]['codigo'], 'MASTER_MUTATION_ID_CONFLICT',
        )
        self.assertTrue(Producto.objects.filter(sku='A053-ID-1').exists())
        self.assertFalse(Producto.objects.filter(sku='A053-ID-2').exists())


class RecepcionMutacionesMaestroConcurrenteA053Tests(
    _RecepcionMutacionesMaestroBase,
    TransactionTestCase,
):
    """El UUID es el cerrojo real cuando dos receptores coinciden a la vez."""

    def tearDown(self):
        connection.close()
        super().tearDown()

    def test_dos_requests_con_el_mismo_uuid_aplican_una_sola_vez(self):
        propuesta = self._crear_producto(sku='A053-CONCURRENTE')
        barrera = threading.Barrier(2, timeout=30)
        resultados = []
        errores = []
        candado = threading.Lock()

        def enviar():
            close_old_connections()
            try:
                barrera.wait()
                respuesta = self._enviar(propuesta)
                with candado:
                    resultados.append(respuesta.data)
            except Exception as exc:  # pragma: no cover - se acredita abajo.
                with candado:
                    errores.append(exc)
            finally:
                close_old_connections()

        hilos = [threading.Thread(target=enviar) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=30)

        self.assertEqual(errores, [])
        self.assertEqual(len(resultados), 2, 'algún receptor no terminó')
        estados = sorted(resultado['detalle'][0]['estado'] for resultado in resultados)
        self.assertEqual(estados, ['CONFIRMADA', 'DUPLICADA'])
        self.assertEqual(Producto.objects.filter(sku='A053-CONCURRENTE').count(), 1)
        self.assertEqual(MutacionMaestro.objects.filter(
            mutacion_id=propuesta['mutacion_id'],
        ).count(), 1)
        self.assertEqual(Auditoria.objects.filter(
            accion='sync.maestro.aplicado',
            correlacion_id=propuesta['mutacion_id'],
        ).count(), 1)
