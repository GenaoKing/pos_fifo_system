from unittest.mock import patch

from django.test import TestCase

from apps.productos.models import Categoria, Producto
from apps.sync.engine import SyncEngine


class DummyResponse:
    status_code = 200
    text = ''

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class SyncEnginePullProductosTests(TestCase):
    def test_pull_productos_crea_y_actualiza_campos_editables_del_portal(self):
        categoria = Categoria.objects.create(nombre='Vasos', descripcion='Linea vasos')
        Producto.objects.create(
            sku='TEST-001',
            codigo_barras='OLD-001',
            nombre='Producto viejo',
            descripcion='Antes del portal',
            categoria=categoria,
            precio_venta='100.00',
            stock_minimo=5,
            activo=True,
            estado='nuevo',
            marca='',
            atributos={},
        )

        payload = {
            'count': 2,
            'next': None,
            'previous': None,
            'results': [
                {
                    'id': 99,
                    'sku': 'TEST-001',
                    'codigo_barras': 'CB-001',
                    'nombre': 'Producto de prueba',
                    'descripcion': 'Editado desde portal',
                    'categoria': 1,
                    'categoria_nombre': 'Vasos',
                    'estado': 'usado',
                    'marca': 'Royal',
                    'precio_venta': '125.00',
                    'stock_minimo': 12,
                    'activo': False,
                    'imagen_url': 'https://cloud.example/media/productos/test.jpg',
                    'atributos': {'color': 'rojo', 'tamano': '16oz'},
                    'fecha_modificacion': '2026-05-24T10:00:00-04:00',
                },
                {
                    'id': 100,
                    'sku': 'TEST-002',
                    'codigo_barras': 'CB-002',
                    'nombre': 'Producto nuevo',
                    'descripcion': '',
                    'categoria': 1,
                    'categoria_nombre': 'Vasos',
                    'estado': 'nuevo',
                    'marca': 'Marca nueva',
                    'precio_venta': '75.00',
                    'stock_minimo': 3,
                    'activo': True,
                    'atributos': {'material': 'plastico'},
                    'fecha_modificacion': '2026-05-24T10:01:00-04:00',
                },
            ],
        }

        engine = SyncEngine(cloud_url='https://cloud.example', token='token')

        with patch('apps.sync.engine.requests.get', return_value=DummyResponse(payload)):
            count = engine._pull_productos()

        producto = Producto.objects.get(sku='TEST-001')
        self.assertEqual(count, 2)
        self.assertEqual(producto.nombre, 'Producto de prueba')
        self.assertEqual(producto.descripcion, 'Editado desde portal')
        self.assertEqual(str(producto.precio_venta), '125.00')
        self.assertEqual(producto.codigo_barras, 'CB-001')
        self.assertFalse(producto.activo)
        self.assertEqual(producto.estado, 'usado')
        self.assertEqual(producto.marca, 'Royal')
        self.assertEqual(producto.stock_minimo, 12)
        self.assertEqual(producto.atributos, {'color': 'rojo', 'tamano': '16oz'})
        self.assertEqual(producto.categoria.nombre, 'Vasos')
        self.assertFalse(producto.imagen)

        producto_nuevo = Producto.objects.get(sku='TEST-002')
        self.assertEqual(producto_nuevo.nombre, 'Producto nuevo')
        self.assertEqual(str(producto_nuevo.precio_venta), '75.00')
        self.assertEqual(producto_nuevo.stock_minimo, 3)
        self.assertEqual(producto_nuevo.marca, 'Marca nueva')
        self.assertEqual(producto_nuevo.atributos, {'material': 'plastico'})
