"""
Comando `descargar_imagenes_productos` — backfill/reparacion de fotos que no
bajaron durante el pull normal (BUG-G, docs/BUGS.md).
"""
import shutil
import tempfile
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from apps.productos.models import Categoria, Producto
from apps.productos.tests.test_miniaturas import imagen_jpeg


class _RespuestaHTTP:
    def __init__(self, payload=None, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.text = ''

    def json(self):
        return self.payload


class _RespuestaImagen:
    def __init__(self, contenido):
        self.content = contenido
        self.status_code = 200


def _paginado(results, next_=None):
    return {'count': len(results), 'next': next_, 'previous': None, 'results': results}


# `requests` es un modulo singleton: parcheando su atributo `.get` una sola
# vez alcanza para todo el codigo que hace `import requests; requests.get(...)`,
# sin importar desde que modulo se importo (engine.py o este comando).
def _con_requests_mockeado(fake_get):
    return patch('requests.get', side_effect=fake_get)


@override_settings(
    MEDIA_ROOT=tempfile.mkdtemp(prefix='descargar-imagenes-'),
    CLOUD_API_URL='https://cloud.example', CLOUD_API_TOKEN='tok',
)
class DescargarImagenesProductosTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.categoria = Categoria.objects.create(nombre='Vasos')
        self.producto = Producto.objects.create(
            sku='BF-001', nombre='Backfill', categoria=self.categoria, precio_venta='10.00',
        )

    @override_settings(CLOUD_API_URL='', CLOUD_API_TOKEN='')
    def test_sin_configuracion_falla_claro(self):
        with self.assertRaises(CommandError):
            call_command('descargar_imagenes_productos', stdout=StringIO())

    def test_dry_run_no_descarga_nada(self):
        item = {'sku': 'BF-001', 'imagen_url': 'https://cloud.example/media/bf-001.jpg'}

        def fake_get(url, **kwargs):
            if 'maestros/productos' in url:
                return _RespuestaHTTP(_paginado([item]))
            raise AssertionError('no deberia pedir la imagen en dry-run')

        with _con_requests_mockeado(fake_get):
            salida = StringIO()
            call_command('descargar_imagenes_productos', stdout=salida)

        self.assertIn('1 producto', salida.getvalue())
        self.assertIn('DRY-RUN BF-001', salida.getvalue())
        self.producto.refresh_from_db()
        self.assertFalse(self.producto.imagen)

    def test_ejecutar_descarga_y_reporta(self):
        item = {'sku': 'BF-001', 'imagen_url': 'https://cloud.example/media/bf-001.jpg'}

        def fake_get(url, **kwargs):
            if 'maestros/productos' in url:
                return _RespuestaHTTP(_paginado([item]))
            return _RespuestaImagen(imagen_jpeg())

        with _con_requests_mockeado(fake_get):
            salida = StringIO()
            call_command('descargar_imagenes_productos', '--ejecutar', stdout=salida)

        self.assertIn('descargadas: 1', salida.getvalue())
        self.assertIn('fallidas:    0', salida.getvalue())
        self.producto.refresh_from_db()
        self.assertTrue(self.producto.imagen)
        self.assertEqual(self.producto.imagen_origen_url, item['imagen_url'])

    def test_producto_no_sincronizado_local_se_omite(self):
        item = {'sku': 'NO-EXISTE-LOCAL', 'imagen_url': 'https://cloud.example/media/x.jpg'}

        def fake_get(url, **kwargs):
            return _RespuestaHTTP(_paginado([item]))

        with _con_requests_mockeado(fake_get):
            salida = StringIO()
            call_command('descargar_imagenes_productos', stdout=salida)

        self.assertIn('0 producto', salida.getvalue())

    def test_ya_descargada_no_se_reprocesa(self):
        Producto.objects.filter(pk=self.producto.pk).update(
            imagen_origen_url='https://cloud.example/media/bf-001.jpg',
        )
        item = {'sku': 'BF-001', 'imagen_url': 'https://cloud.example/media/bf-001.jpg'}

        def fake_get(url, **kwargs):
            if 'maestros/productos' in url:
                return _RespuestaHTTP(_paginado([item]))
            raise AssertionError('no deberia volver a pedir la misma imagen')

        with _con_requests_mockeado(fake_get):
            salida = StringIO()
            call_command('descargar_imagenes_productos', '--ejecutar', stdout=salida)

        self.assertIn('0 producto', salida.getvalue())

    def test_pagina_siguiente_se_recorre(self):
        item_pagina_1 = {'sku': 'YA-EXISTE-NO', 'imagen_url': None}
        item_pagina_2 = {'sku': 'BF-001', 'imagen_url': 'https://cloud.example/media/bf-001.jpg'}

        def fake_get(url, **kwargs):
            if url == 'https://cloud.example/api/v1/maestros/productos/':
                return _RespuestaHTTP(_paginado(
                    [item_pagina_1], next_='https://cloud.example/api/v1/maestros/productos/?page=2',
                ))
            if 'page=2' in url:
                return _RespuestaHTTP(_paginado([item_pagina_2]))
            return _RespuestaImagen(imagen_jpeg())

        with _con_requests_mockeado(fake_get):
            salida = StringIO()
            call_command('descargar_imagenes_productos', stdout=salida)

        self.assertIn('1 producto', salida.getvalue())
        self.assertIn('DRY-RUN BF-001', salida.getvalue())
