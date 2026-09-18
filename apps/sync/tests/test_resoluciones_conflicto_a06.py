"""Aplicación local, idempotencia y validación runtime del retorno A06."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.negocios.models import Negocio
from apps.productos.models import Categoria, Producto, productos_vendibles
from apps.sucursales.models import Sucursal
from apps.sync.engine import SyncEngine
from apps.sync.models import MutacionMaestro, ResolucionConflictoMaestro, VersionMaestro


User = get_user_model()


class _Resp:
    status_code = 200
    text = ''

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


@override_settings(SUCURSAL_CODIGO='A06-RES')
class PullResolucionesConflictoA06Tests(TestCase):
    def setUp(self):
        self.negocio = Negocio.objects.create(nombre='Negocio pull A06', slug='pull-a06')
        self.sucursal = Sucursal.objects.create(
            negocio=self.negocio, codigo='A06-RES', nombre='Sucursal pull A06', activa=True,
        )
        self.actor = User.objects.create_user(
            'operador_pull_a06', 'operador-pull-a06@example.test', 'x', rol='CAJERA',
        )
        self.categoria = Categoria.objects.create(nombre='Categoría pull A06')
        self.producto = Producto.objects.create(
            sku='PULL-A06-001', codigo_barras='PULL-A06-001', nombre='Producto pull A06',
            descripcion='', categoria=self.categoria, precio_venta='50.00', stock_minimo=1,
            activo=True, atributos={}, estado='nuevo', marca='',
        )
        self.mutacion = MutacionMaestro.objects.create(
            entidad=MutacionMaestro.Entidad.PRODUCTO,
            entidad_id=self.producto.pk,
            operacion=MutacionMaestro.Operacion.ACTUALIZAR,
            revision_base='2026-09-17T12:00:00+00:00',
            delta={'nombre': {'before': 'Antes', 'after': 'Después'}},
            estado=MutacionMaestro.Estado.CONFLICTO,
            actor=self.actor,
            actor_username=self.actor.username,
            sucursal=self.sucursal,
            sucursal_codigo=self.sucursal.codigo,
            tenant_key=self.negocio.slug,
            cloud_entidad_id=44,
            cloud_revision='2026-09-17T12:05:00+00:00',
        )

    def _payload(self, **cambios):
        fila = {
            'schema_version': 'master.conflict-resolution-sync.v1',
            'id': 71,
            'fecha_modificacion': timezone.now().isoformat(),
            'mutacion_id': str(self.mutacion.mutacion_id),
            'accion': 'CONSERVAR_CLOUD',
            'motivo': 'El catálogo cloud fue revisado y es autoritativo.',
            'actor_username': 'admin_cloud_a06',
            'cloud_revision_observada': self.mutacion.cloud_revision,
            'cloud_revision_resultante': '2026-09-17T12:06:00+00:00',
            'cloud_entidad_id': 44,
        }
        fila.update(cambios)
        return {
            'schema_version': 'master.conflict-resolution-sync.v1',
            'results': [fila],
            'next': None,
        }

    @patch('apps.sync.engine.requests.get')
    def test_pull_crea_ledger_idempotente_y_libera_catalogo_local(self, mock_get):
        self.assertFalse(productos_vendibles().filter(pk=self.producto.pk).exists())
        mock_get.side_effect = [
            _Resp(self._payload()),
            _Resp({
                'schema_version': 'master.conflict-resolution-sync.v1',
                'results': [],
                'next': None,
            }),
        ]
        engine = SyncEngine(cloud_url='https://cloud.example', token='a06-token')

        primero = engine._pull_resoluciones_conflicto()
        segundo = engine._pull_resoluciones_conflicto()

        self.assertTrue(primero['ok'])
        self.assertEqual(primero['count'], 1)
        self.assertTrue(segundo['ok'])
        self.assertEqual(segundo['count'], 0)
        self.assertEqual(ResolucionConflictoMaestro.objects.filter(mutacion=self.mutacion).count(), 1)
        self.assertTrue(productos_vendibles().filter(pk=self.producto.pk).exists())
        cursor = VersionMaestro.objects.get(tabla='resoluciones_conflicto')
        self.assertEqual(cursor.ultimo_id, 71)

    @patch('apps.sync.engine.requests.get')
    def test_schema_desconocido_falla_cerrado_y_no_libera_conflicto(self, mock_get):
        mock_get.return_value = _Resp(self._payload(schema_version='master.conflict-resolution-sync.v2'))

        resultado = SyncEngine(
            cloud_url='https://cloud.example', token='a06-token',
        )._pull_resoluciones_conflicto()

        self.assertFalse(resultado['ok'])
        self.assertIn('schema_version desconocido', resultado['error'])
        self.assertFalse(ResolucionConflictoMaestro.objects.filter(mutacion=self.mutacion).exists())
        self.assertFalse(productos_vendibles().filter(pk=self.producto.pk).exists())
        self.assertFalse(VersionMaestro.objects.get(tabla='resoluciones_conflicto').ultima_version)

    @patch('apps.sync.engine.requests.get')
    def test_pull_de_maestros_transporta_bajas_y_sus_motivos(self, mock_get):
        instante = timezone.now().isoformat()
        mock_get.side_effect = [
            _Resp([{
                'id': 811,
                'nombre': 'Categoría inactiva cloud A06',
                'descripcion': '',
                'activa': False,
                'motivo_inactivacion': 'La línea fue retirada del catálogo cloud.',
                'inactivado_at': instante,
                'tipo_negocio': '',
                'atributos_configurados': {},
                'fecha_modificacion': instante,
            }]),
            _Resp([{
                'id': 812,
                'sku': 'SYNC-INACTIVO-A06',
                'codigo_barras': 'SYNC-INACTIVO-A06',
                'nombre': 'Producto inactivo cloud A06',
                'descripcion': '',
                'categoria': 811,
                'categoria_nombre': 'Categoría inactiva cloud A06',
                'precio_venta': '12.00',
                'activo': False,
                'motivo_inactivacion': 'El proveedor descontinuó esta referencia.',
                'inactivado_at': instante,
                'estado': 'nuevo',
                'marca': '',
                'stock_minimo': 1,
                'atributos': {},
                'pendiente_revision': False,
                'fecha_modificacion': instante,
            }]),
        ]
        engine = SyncEngine(cloud_url='https://cloud.example', token='a06-token')

        categorias = engine._pull_categorias()
        productos = engine._pull_productos()

        categoria = Categoria.objects.get(origen_cloud_id=811)
        producto = Producto.objects.get(origen_cloud_id=812)
        self.assertEqual(categorias['count'], 1)
        self.assertEqual(productos['count'], 1)
        self.assertFalse(categoria.activa)
        self.assertEqual(
            categoria.motivo_inactivacion,
            'La línea fue retirada del catálogo cloud.',
        )
        self.assertIsNotNone(categoria.inactivado_at)
        self.assertFalse(producto.activo)
        self.assertEqual(
            producto.motivo_inactivacion,
            'El proveedor descontinuó esta referencia.',
        )
        self.assertIsNotNone(producto.inactivado_at)
