"""Envío A05.3: lease, ACK incierto, CAS encadenado y dependencias."""
import uuid
from decimal import Decimal
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.configuracion.models import ConfiguracionNegocio
from apps.productos.models import Categoria, Producto
from apps.productos.services import (
    crear_categoria_local,
    crear_producto_local,
    editar_producto_local,
)
from apps.sucursales.models import Sucursal
from apps.sync.engine import SyncEngine
from apps.sync.models import MutacionMaestro


User = get_user_model()
REVISION_1 = '2026-09-16T12:00:00+00:00'
REVISION_2 = '2026-09-16T12:01:00+00:00'
REVISION_3 = '2026-09-16T12:02:00+00:00'


class _Respuesta:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code
        self.text = str(data)

    def json(self):
        return self._data


class PushMutacionesMaestroA053Tests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_user(
            'admin_a053_local', 'admin-a053-local@example.test', 'x', rol='ADMIN',
        )
        self.sucursal = Sucursal.objects.create(
            codigo='A053-LOCAL', nombre='Sucursal local A053', activa=True,
        )
        ConfiguracionNegocio.objects.create(
            sucursal=self.sucursal, nombre_negocio='Pruebas A05.3',
        )
        self.categoria = Categoria.objects.create(
            nombre='Categoría local A053', origen_cloud_id=81,
            revision_cloud=REVISION_1,
        )
        self.producto = Producto.objects.create(
            sku='A053-LOCAL', codigo_barras='A053-LOCAL', nombre='Producto local',
            descripcion='', categoria=self.categoria,
            precio_venta=Decimal('20.00'), stock_minimo=1, activo=True,
            atributos={}, estado='nuevo', marca='', origen_cloud_id=91,
            revision_cloud=REVISION_1,
        )
        self.engine = SyncEngine(cloud_url='https://cloud.test', token='token', lease_seconds=300)

    def _editar_producto(self, *, nombre='Producto editado', precio='22.00'):
        return editar_producto_local(
            actor=self.actor,
            sucursal=self.sucursal,
            producto_id=self.producto.pk,
            datos={
                'codigo_barras': self.producto.codigo_barras,
                'nombre': nombre,
                'descripcion': self.producto.descripcion,
                'categoria_id': self.categoria.pk,
                'precio_venta': precio,
                'stock_minimo': self.producto.stock_minimo,
                'activo': self.producto.activo,
                'atributos': self.producto.atributos,
                'estado': self.producto.estado,
                'marca': self.producto.marca,
            },
            mutacion_id=uuid.uuid4(),
        ).mutacion

    @staticmethod
    def _ack(mutacion, *, estado='CONFIRMADA', cloud_id=91, revision=REVISION_2):
        return _Respuesta({
            'detalle': [{
                'mutacion_id': str(mutacion.mutacion_id),
                'estado': estado,
                'cloud_entidad_id': cloud_id,
                'cloud_revision': revision,
            }],
        })

    @mock.patch('apps.sync.engine.requests.post')
    def test_confirma_sella_revision_cloud_y_usa_endpoint_separado(self, post):
        mutacion = self._editar_producto()
        post.return_value = self._ack(mutacion)

        resultado = self.engine.push_mutaciones_maestro()

        self.assertEqual(resultado, {
            'procesadas': 1, 'confirmadas': 1, 'duplicadas': 0,
            'conflictos': 0, 'rechazadas': 0, 'fallidas': 0,
        })
        mutacion.refresh_from_db()
        self.producto.refresh_from_db()
        self.assertEqual(mutacion.estado, MutacionMaestro.Estado.CONFIRMADA)
        self.assertEqual(mutacion.cloud_entidad_id, 91)
        self.assertEqual(mutacion.cloud_revision, REVISION_2)
        self.assertEqual(self.producto.origen_cloud_id, 91)
        self.assertEqual(self.producto.revision_cloud, REVISION_2)
        self.assertTrue(post.call_args.args[0].endswith('/api/v1/sync/mutaciones-maestro/'))
        propuesta = post.call_args.kwargs['json']['mutaciones'][0]
        self.assertEqual(propuesta['revision_base'], REVISION_1)
        self.assertEqual(propuesta['cloud_entidad_id'], 91)
        self.assertEqual(propuesta['categoria_cloud_id'], 81)

    @mock.patch('apps.sync.engine.requests.post')
    def test_ack_perdido_reintenta_mismo_uuid_y_acepta_duplicada(self, post):
        mutacion = self._editar_producto()
        post.side_effect = requests.ConnectionError('ACK perdido')

        primero = self.engine.push_mutaciones_maestro()

        mutacion.refresh_from_db()
        self.assertEqual(primero['fallidas'], 1)
        self.assertEqual(mutacion.estado, MutacionMaestro.Estado.PENDIENTE)
        self.assertEqual(mutacion.intentos, 1)

        post.side_effect = None
        post.return_value = self._ack(mutacion, estado='DUPLICADA')
        segundo = self.engine.push_mutaciones_maestro()

        mutacion.refresh_from_db()
        self.assertEqual(segundo['duplicadas'], 1)
        self.assertEqual(mutacion.estado, MutacionMaestro.Estado.CONFIRMADA)
        enviados = [
            llamada.kwargs['json']['mutaciones'][0]['mutacion_id']
            for llamada in post.call_args_list
        ]
        self.assertEqual(enviados, [str(mutacion.mutacion_id), str(mutacion.mutacion_id)])

    @mock.patch('apps.sync.engine.requests.post')
    def test_rebasa_siguiente_propuesta_de_la_misma_entidad_en_orden(self, post):
        primera = self._editar_producto(nombre='Nombre uno', precio='22.00')
        self.producto.refresh_from_db()
        segunda = self._editar_producto(nombre='Nombre dos', precio='23.00')
        self.assertEqual(primera.revision_base, REVISION_1)
        self.assertEqual(segunda.revision_base, REVISION_1)

        post.side_effect = [
            self._ack(primera, revision=REVISION_2),
            self._ack(segunda, revision=REVISION_3),
        ]

        self.assertEqual(self.engine.push_mutaciones_maestro()['confirmadas'], 1)
        segunda.refresh_from_db()
        self.assertEqual(segunda.revision_base, REVISION_2)
        self.assertEqual(self.engine.push_mutaciones_maestro()['confirmadas'], 1)
        segunda.refresh_from_db()
        self.assertEqual(segunda.estado, MutacionMaestro.Estado.CONFIRMADA)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.revision_cloud, REVISION_3)

    @mock.patch('apps.sync.engine.requests.post')
    def test_categoria_local_se_confirma_antes_del_producto_dependiente(self, post):
        categoria = crear_categoria_local(
            actor=self.actor,
            sucursal=self.sucursal,
            datos={'nombre': 'Categoría nueva A053'},
            mutacion_id=uuid.uuid4(),
        ).entidad
        producto = crear_producto_local(
            actor=self.actor,
            sucursal=self.sucursal,
            datos={
                'nombre': 'Producto dependiente A053',
                'categoria_id': categoria.pk,
                'precio_venta': '50.00',
            },
            mutacion_id=uuid.uuid4(),
        ).entidad

        def responder(*_args, **kwargs):
            propuesta = kwargs['json']['mutaciones'][0]
            if propuesta['entidad'] == MutacionMaestro.Entidad.CATEGORIA:
                return self._ack(
                    MutacionMaestro.objects.get(mutacion_id=propuesta['mutacion_id']),
                    cloud_id=701,
                    revision=REVISION_2,
                )
            self.assertEqual(propuesta['categoria_cloud_id'], 701)
            return self._ack(
                MutacionMaestro.objects.get(mutacion_id=propuesta['mutacion_id']),
                cloud_id=702,
                revision=REVISION_3,
            )

        post.side_effect = responder

        self.assertEqual(self.engine.push_mutaciones_maestro()['confirmadas'], 1)
        categoria.refresh_from_db()
        self.assertEqual(categoria.origen_cloud_id, 701)
        self.assertEqual(self.engine.push_mutaciones_maestro()['confirmadas'], 1)
        producto.refresh_from_db()
        self.assertEqual(producto.origen_cloud_id, 702)

    @mock.patch('apps.sync.engine.requests.post')
    def test_conflicto_cloud_es_terminal_y_bloquea_propuesta_sin_reintento(self, post):
        mutacion = self._editar_producto()
        post.return_value = _Respuesta({
            'detalle': [{
                'mutacion_id': str(mutacion.mutacion_id),
                'estado': 'CONFLICTO',
                'codigo': 'MASTER_REVISION_CONFLICT',
                'error': 'El maestro cambió en cloud.',
                'cloud_entidad_id': 91,
                'cloud_revision': REVISION_2,
            }],
        })

        resultado = self.engine.push_mutaciones_maestro()

        mutacion.refresh_from_db()
        self.assertEqual(resultado['conflictos'], 1)
        self.assertEqual(mutacion.estado, MutacionMaestro.Estado.CONFLICTO)
        self.assertEqual(mutacion.codigo_resultado, 'MASTER_REVISION_CONFLICT')
        self.assertEqual(mutacion.intentos, 0)
