from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from apps.configuracion.utils import get_config
from apps.productos.models import Categoria, Producto
from apps.sync.engine import SyncEngine


class DummyResponse:
    status_code = 200
    text = ''

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class PrecioProductoCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.usuario = User.objects.create_user(
            username='cajera_precio',
            email='cajera_precio@example.com',
            password='pass',
            rol='CAJERA',
            activo=True,
        )
        self.client.force_login(self.usuario)

        self.categoria = Categoria.objects.create(nombre='Vasos')
        self.producto = Producto.objects.create(
            sku='TEST-PRICE-001',
            codigo_barras='PRICE-001',
            nombre='Vaso cache test',
            descripcion='Precio inicial',
            categoria=self.categoria,
            precio_venta='100.00',
            stock_minimo=5,
            activo=True,
            estado='nuevo',
            marca='',
            atributos={},
        )

    def tearDown(self):
        cache.clear()

    def _pull_precio(self, precio):
        payload = {
            'count': 1,
            'next': None,
            'previous': None,
            'results': [
                {
                    'id': 99,
                    'sku': self.producto.sku,
                    'codigo_barras': self.producto.codigo_barras,
                    'nombre': self.producto.nombre,
                    'descripcion': 'Editado desde portal',
                    'categoria': self.categoria.id,
                    'categoria_nombre': self.categoria.nombre,
                    'estado': 'nuevo',
                    'marca': 'Royal',
                    'precio_venta': precio,
                    'stock_minimo': 8,
                    'activo': True,
                    'atributos': {'color': 'rojo'},
                    'fecha_modificacion': '2026-05-24T10:00:00-04:00',
                }
            ],
        }
        engine = SyncEngine(cloud_url='https://cloud.example', token='token')

        with patch('apps.sync.engine.requests.get', return_value=DummyResponse(payload)):
            return engine._pull_productos()

    def test_pos_lee_precio_actualizado_despues_del_pull_con_config_cacheada(self):
        get_config()

        count = self._pull_precio('125.00')

        self.assertEqual(count, 1)
        self.producto.refresh_from_db()
        self.assertEqual(str(self.producto.precio_venta), '125.00')

        busqueda = self.client.get('/pos/api/buscar/', {'q': 'Vaso cache'})
        scanner = self.client.get(f'/pos/api/producto/{self.producto.codigo_barras}/')

        self.assertEqual(busqueda.status_code, 200)
        self.assertEqual(scanner.status_code, 200)
        self.assertEqual(busqueda.json()['productos'][0]['precio_venta'], 125.0)
        self.assertEqual(busqueda.json()['productos'][0]['precio_formateado'], '$125.00')
        self.assertEqual(scanner.json()['producto']['precio_venta'], 125.0)
        self.assertEqual(scanner.json()['producto']['precio_formateado'], '$125.00')
