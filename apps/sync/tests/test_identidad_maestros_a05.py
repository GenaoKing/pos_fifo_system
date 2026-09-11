from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.clientes.models import Cliente
from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal
from apps.sync.engine import SyncEngine
from apps.sync.models import DiferidoSync


class _Response:
    status_code = 200
    text = ''

    def __init__(self, items):
        self._payload = {
            'count': len(items),
            'next': None,
            'previous': None,
            'results': items,
        }

    def json(self):
        return self._payload


@override_settings(
    SUCURSAL_CODIGO='A05-B',
    SYNC_ENABLED=True,
    CLOUD_API_URL='https://cloud.a05.test',
    CLOUD_API_TOKEN='a05-token',
)
class AdopcionProductoA05Tests(TestCase):
    def setUp(self):
        self.sucursal_a = Sucursal.objects.create(
            codigo='A05-A', nombre='Sucursal A', activa=True,
        )
        self.sucursal_b = Sucursal.objects.create(
            codigo='A05-B', nombre='Sucursal B', activa=True,
        )
        self.categoria = Categoria.objects.create(
            nombre='Categoria local', origen_cloud_id=71,
        )
        self.engine = SyncEngine()

    def _producto_item(
        self, cloud_id=901, sku='A05-SKU', nombre='Nombre cloud',
        segundos=1, pendiente_revision=False,
    ):
        return {
            'id': cloud_id,
            'sku': sku,
            'nombre': nombre,
            'descripcion': 'Descripcion cloud',
            'precio_venta': '25.00',
            'codigo_barras': None,
            'categoria': 71,
            # Deliberadamente distinto: la relacion debe resolverse por ID.
            'categoria_nombre': 'Nombre cloud de categoria',
            'activo': True,
            'estado': 'nuevo',
            'marca': 'A05',
            'stock_minimo': 3,
            'atributos': {'origen': 'cloud'},
            'pendiente_revision': pendiente_revision,
            'fecha_modificacion': (
                timezone.now() + timedelta(seconds=segundos)
            ).isoformat(),
        }

    def _pull(self, item):
        with mock.patch(
            'apps.sync.engine.requests.get',
            return_value=_Response([item]),
        ):
            return self.engine._pull_productos()

    def test_primera_sync_adopta_sku_exacto_y_preserva_procedencia_bug_h(self):
        local = Producto.objects.create(
            sku='A05-SKU',
            nombre='Nombre local',
            categoria=self.categoria,
            precio_venta=Decimal('10.00'),
            origen_sucursal=self.sucursal_a,
            pendiente_revision=True,
        )

        resultado = self._pull(self._producto_item())

        self.assertEqual(resultado['count'], 1)
        self.assertEqual(Producto.objects.count(), 1)
        local.refresh_from_db()
        self.assertEqual(local.origen_cloud_id, 901)
        self.assertEqual(local.nombre, 'Nombre cloud')
        self.assertEqual(local.categoria_id, self.categoria.id)
        self.assertEqual(local.origen_sucursal_id, self.sucursal_a.id)
        self.assertTrue(local.pendiente_revision)

    def test_renombre_y_replay_actualizan_la_misma_fila(self):
        local = Producto.objects.create(
            sku='A05-SKU',
            nombre='Nombre local',
            categoria=self.categoria,
            precio_venta='10.00',
        )
        self._pull(self._producto_item())

        renombrado = self._producto_item(
            nombre='Nombre cloud renombrado', segundos=60,
        )
        self._pull(renombrado)
        self._pull(renombrado)

        self.assertEqual(Producto.objects.count(), 1)
        local.refresh_from_db()
        self.assertEqual(local.origen_cloud_id, 901)
        self.assertEqual(local.nombre, 'Nombre cloud renombrado')

    def test_sku_sellado_por_otra_identidad_se_difiere_sin_pisar(self):
        local = Producto.objects.create(
            sku='A05-SKU',
            nombre='Pertenece a otro cloud ID',
            categoria=self.categoria,
            precio_venta='10.00',
            origen_cloud_id=999,
        )

        resultado = self._pull(self._producto_item(cloud_id=901))

        self.assertEqual(resultado['count'], 0)
        self.assertEqual(resultado['diferidos_pendientes'], 1)
        local.refresh_from_db()
        self.assertEqual(local.origen_cloud_id, 999)
        self.assertEqual(local.nombre, 'Pertenece a otro cloud ID')
        diferido = DiferidoSync.objects.get()
        self.assertEqual(diferido.sucursal_codigo, 'A05-B')
        self.assertIn('MASTER_NATURAL_ID_CONFLICT', diferido.ultimo_error)

    def test_identidad_existente_con_otro_sku_se_difiere(self):
        local = Producto.objects.create(
            sku='A05-ORIGINAL',
            nombre='Original',
            categoria=self.categoria,
            precio_venta='10.00',
            origen_cloud_id=901,
        )

        resultado = self._pull(self._producto_item(sku='A05-NUEVO'))

        self.assertEqual(resultado['count'], 0)
        self.assertFalse(Producto.objects.filter(sku='A05-NUEVO').exists())
        local.refresh_from_db()
        self.assertEqual(local.sku, 'A05-ORIGINAL')
        self.assertEqual(local.nombre, 'Original')
        self.assertIn(
            'MASTER_SKU_IMMUTABLE',
            DiferidoSync.objects.get().ultimo_error,
        )

    def test_stub_pendiente_del_cloud_no_se_convierte_en_identidad(self):
        local = Producto.objects.create(
            sku='A05-SKU',
            nombre='Producto real local',
            categoria=self.categoria,
            precio_venta='10.00',
            origen_sucursal=self.sucursal_a,
        )

        self._pull(self._producto_item(pendiente_revision=True))

        local.refresh_from_db()
        self.assertIsNone(local.origen_cloud_id)
        self.assertEqual(local.nombre, 'Producto real local')
        self.assertEqual(local.origen_sucursal_id, self.sucursal_a.id)


@override_settings(
    SUCURSAL_CODIGO='A05-B',
    SYNC_ENABLED=True,
    CLOUD_API_URL='https://cloud.a05.test',
    CLOUD_API_TOKEN='a05-token',
)
class AmbiguedadNaturalA05Tests(TestCase):
    def test_dos_clientes_sin_cedula_iguales_se_difieren_explicitamente(self):
        Cliente.objects.create(nombre='Cliente repetido', tipo='PERSONAL')
        Cliente.objects.create(nombre='Cliente repetido', tipo='PERSONAL')
        item = {
            'id': 801,
            'nombre': 'Cliente repetido',
            'tipo': 'PERSONAL',
            'cedula_rnc': None,
            'telefono': None,
            'direccion': None,
            'limite_credito': '0.00',
            'plazo_credito_dias': 30,
            'condiciones_pago': None,
            'notas': None,
            'activo': True,
            'fecha_modificacion': timezone.now().isoformat(),
        }

        with mock.patch(
            'apps.sync.engine.requests.get',
            return_value=_Response([item]),
        ):
            resultado = SyncEngine()._pull_clientes()

        self.assertEqual(resultado['count'], 0)
        self.assertEqual(Cliente.objects.count(), 2)
        self.assertFalse(Cliente.objects.filter(origen_cloud_id=801).exists())
        self.assertIn(
            'MASTER_NATURAL_AMBIGUOUS',
            DiferidoSync.objects.get().ultimo_error,
        )
