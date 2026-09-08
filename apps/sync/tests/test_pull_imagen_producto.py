"""
Descarga de la foto de producto en el pull (BUG-G, docs/BUGS.md): la foto
subida desde el portal cloud (apps/api/views/maestros.py::ProductoViewSet.imagen)
baja a la sucursal dentro de `_pull_productos`, best-effort -- nunca difiere
el item ni bloquea el cursor.
"""
import shutil
import tempfile
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings

from apps.productos.models import Categoria, Producto
from apps.productos.tests.test_miniaturas import imagen_jpeg
from apps.sync.engine import SyncEngine


def make_engine():
    return SyncEngine(cloud_url='https://cloud.example', token='token')


def paginated(results):
    return {'count': len(results), 'next': None, 'previous': None, 'results': results}


class RespuestaHTTP:
    def __init__(self, payload=None, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class RespuestaImagen:
    def __init__(self, contenido=b'fake-bytes', status_code=200):
        self.content = contenido
        self.status_code = status_code


def _item_producto(sku='IMG-001', imagen_url=None, **extra):
    item = {
        'id': 1, 'sku': sku, 'codigo_barras': '', 'nombre': f'Producto {sku}',
        'descripcion': '', 'categoria': 1, 'categoria_nombre': 'Vasos',
        'estado': 'nuevo', 'marca': '', 'precio_venta': '10.00', 'stock_minimo': 5,
        'activo': True, 'atributos': {}, 'fecha_modificacion': '2026-08-24T10:00:00-04:00',
    }
    if imagen_url is not None:
        item['imagen_url'] = imagen_url
    item.update(extra)
    return item


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='pull-imagen-'))
class DescargarImagenProductoTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        Categoria.objects.create(nombre='Vasos')

    def _pull_con(self, item, respuesta_imagen):
        """Corre _pull_productos con el listado paginado + una sola respuesta de imagen."""
        payload_lista = RespuestaHTTP(paginated([item]))

        def fake_get(url, **kwargs):
            if url == 'https://cloud.example/api/v1/maestros/productos/':
                return payload_lista
            return respuesta_imagen

        with patch('apps.sync.engine.requests.get', side_effect=fake_get):
            return make_engine()._pull_productos()

    def test_descarga_aplica_y_sella_la_url(self):
        item = _item_producto(imagen_url='https://cloud.example/media/img-001.jpg')
        resultado = self._pull_con(item, RespuestaImagen(imagen_jpeg()))

        self.assertTrue(resultado['ok'])
        producto = Producto.objects.get(sku='IMG-001')
        self.assertTrue(producto.imagen)
        self.assertTrue(producto.imagen_miniatura)
        self.assertEqual(producto.imagen_origen_url, 'https://cloud.example/media/img-001.jpg')

    def test_sin_imagen_url_no_intenta_nada(self):
        item = _item_producto()  # sin imagen_url
        resultado = self._pull_con(item, RespuestaImagen())

        self.assertTrue(resultado['ok'])
        producto = Producto.objects.get(sku='IMG-001')
        self.assertFalse(producto.imagen)
        self.assertEqual(producto.imagen_origen_url, '')

    def test_fallo_de_descarga_no_difiere_el_producto_ni_sella_nada(self):
        item = _item_producto(imagen_url='https://cloud.example/media/img-001.jpg')
        resultado = self._pull_con(item, RespuestaImagen(status_code=500))

        self.assertTrue(resultado['ok'], 'el producto (texto) se aplica igual')
        self.assertEqual(resultado['count'], 1)
        self.assertIsNone(resultado['bloqueo'], 'una foto que falla no debe congelar el cursor')

        producto = Producto.objects.get(sku='IMG-001')
        self.assertFalse(producto.imagen)
        self.assertEqual(producto.imagen_origen_url, '', 'no se sella: reintenta despues')

    def test_excepcion_de_red_tampoco_bloquea_el_producto(self):
        import requests as requests_mod

        item = _item_producto(imagen_url='https://cloud.example/media/img-001.jpg')

        def fake_get(url, **kwargs):
            if url == 'https://cloud.example/api/v1/maestros/productos/':
                return RespuestaHTTP(paginated([item]))
            raise requests_mod.ConnectionError('boom')

        with patch('apps.sync.engine.requests.get', side_effect=fake_get):
            resultado = make_engine()._pull_productos()

        self.assertTrue(resultado['ok'])
        self.assertEqual(resultado['count'], 1)
        self.assertFalse(Producto.objects.get(sku='IMG-001').imagen)

    def test_misma_url_no_vuelve_a_descargar(self):
        categoria = Categoria.objects.get(nombre='Vasos')
        Producto.objects.create(
            sku='IMG-001', nombre='Ya tiene foto', categoria=categoria,
            precio_venta='10.00', imagen_origen_url='https://cloud.example/media/img-001.jpg',
        )
        item = _item_producto(imagen_url='https://cloud.example/media/img-001.jpg')

        mock_imagen = Mock(side_effect=AssertionError('no deberia pedir la imagen de nuevo'))

        def fake_get(url, **kwargs):
            if url == 'https://cloud.example/api/v1/maestros/productos/':
                return RespuestaHTTP(paginated([item]))
            return mock_imagen(url)

        with patch('apps.sync.engine.requests.get', side_effect=fake_get):
            resultado = make_engine()._pull_productos()

        self.assertTrue(resultado['ok'])

    def test_cloud_sin_foto_no_borra_la_local(self):
        """El campo simplemente no viaja en el payload (producto sin imagen en cloud)."""
        categoria = Categoria.objects.get(nombre='Vasos')
        producto_local = Producto.objects.create(
            sku='IMG-001', nombre='Con foto local', categoria=categoria,
            precio_venta='10.00',
        )
        from django.core.files.base import ContentFile
        producto_local.imagen.save('local.jpg', ContentFile(imagen_jpeg()), save=True)
        self.assertTrue(producto_local.imagen)

        item = _item_producto()  # sin imagen_url: el cloud no tiene foto
        self._pull_con(item, RespuestaImagen())

        producto_local.refresh_from_db()
        self.assertTrue(producto_local.imagen, 'la foto local no debe borrarse')
