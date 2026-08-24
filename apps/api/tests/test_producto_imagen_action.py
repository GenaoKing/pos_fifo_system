"""
POST/DELETE /api/v1/maestros/productos/<id>/imagen/ — subida de foto desde
el portal cloud (con miniatura automatica, via Producto.save()).
"""
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.productos.models import Categoria, Producto
from apps.productos.tests.test_miniaturas import imagen_jpeg
from apps.sucursales.models import Sucursal

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='producto-imagen-action-'))
class ProductoImagenActionTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.categoria = Categoria.objects.create(nombre='Vasos')
        self.producto = Producto.objects.create(
            sku='IMG-001', nombre='Con foto', categoria=self.categoria,
            precio_venta='100.00',
        )
        self.url = f'/api/v1/maestros/productos/{self.producto.id}/imagen/'

        self.admin = User.objects.create_user(
            username='admin_img', email='admin_img@test.local',
            password='x', rol='ADMIN', activo=True,
        )
        self.sucursal_user = User.objects.create_user(
            username='svc_img', email='svc_img@test.local',
            password='x', rol='CAJERA', activo=True,
        )
        self.sucursal = Sucursal.objects.create(
            codigo='IMG-SUC', nombre='Sucursal Img', activa=True,
            usuario_servicio=self.sucursal_user,
        )
        self.sucursal_token = Token.objects.create(user=self.sucursal_user)

    def api(self, user=None, token=None):
        client = APIClient()
        if token:
            client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        elif user:
            client.force_authenticate(user=user)
        return client

    def _archivo(self, nombre='foto.jpg'):
        return SimpleUploadedFile(nombre, imagen_jpeg(), 'image/jpeg')

    def test_admin_sube_imagen_y_recibe_la_miniatura(self):
        response = self.api(user=self.admin).post(
            self.url, {'imagen': self._archivo()}, format='multipart',
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.data['imagen_url'])
        self.assertIsNotNone(response.data['imagen_thumb_url'])
        self.assertNotEqual(response.data['imagen_url'], response.data['imagen_thumb_url'])

        self.producto.refresh_from_db()
        self.assertTrue(self.producto.imagen)
        self.assertTrue(self.producto.imagen_miniatura)

    def test_archivo_que_no_es_imagen_da_400(self):
        archivo_malo = SimpleUploadedFile('nota.txt', b'esto no es una imagen', 'text/plain')
        response = self.api(user=self.admin).post(
            self.url, {'imagen': archivo_malo}, format='multipart',
        )
        self.assertEqual(response.status_code, 400)

        self.producto.refresh_from_db()
        self.assertFalse(self.producto.imagen)

    def test_sin_archivo_da_400(self):
        response = self.api(user=self.admin).post(self.url, {}, format='multipart')
        self.assertEqual(response.status_code, 400)

    def test_token_de_sucursal_no_puede_subir_imagen(self):
        response = self.api(token=self.sucursal_token).post(
            self.url, {'imagen': self._archivo()}, format='multipart',
        )
        self.assertEqual(response.status_code, 403)

    def test_sin_autenticar_da_401(self):
        response = self.api().post(self.url, {'imagen': self._archivo()}, format='multipart')
        self.assertEqual(response.status_code, 401)

    def test_subir_de_nuevo_reemplaza_y_borra_el_original_anterior(self):
        self.api(user=self.admin).post(self.url, {'imagen': self._archivo('primera.jpg')}, format='multipart')
        self.producto.refresh_from_db()
        primer_nombre = self.producto.imagen.name
        storage = self.producto.imagen.storage
        self.assertTrue(storage.exists(primer_nombre))

        self.api(user=self.admin).post(self.url, {'imagen': self._archivo('segunda.jpg')}, format='multipart')
        self.producto.refresh_from_db()

        self.assertNotEqual(self.producto.imagen.name, primer_nombre)
        self.assertFalse(storage.exists(primer_nombre), 'el original anterior queda huerfano')

    def test_delete_borra_imagen_y_miniatura_del_storage(self):
        self.api(user=self.admin).post(self.url, {'imagen': self._archivo()}, format='multipart')
        self.producto.refresh_from_db()
        nombre_imagen = self.producto.imagen.name
        nombre_miniatura = self.producto.imagen_miniatura.name
        storage = self.producto.imagen.storage

        response = self.api(user=self.admin).delete(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['imagen_url'])
        self.assertIsNone(response.data['imagen_thumb_url'])
        self.assertFalse(storage.exists(nombre_imagen))
        self.assertFalse(storage.exists(nombre_miniatura))

        self.producto.refresh_from_db()
        self.assertFalse(self.producto.imagen)
        self.assertFalse(self.producto.imagen_miniatura)

    def test_delete_sin_imagen_no_revienta(self):
        response = self.api(user=self.admin).delete(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['imagen_url'])

    def test_subir_imagen_no_libera_un_stub_pendiente_de_revision(self):
        """La foto no es "completar" el producto: precio/categoria siguen sin revisar."""
        stub = Producto.objects.create(
            sku='STUB-IMG', nombre='STUB-IMG', categoria=Categoria.get_sin_clasificar(),
            precio_venta='0.00', pendiente_revision=True,
        )
        response = self.api(user=self.admin).post(
            f'/api/v1/maestros/productos/{stub.id}/imagen/',
            {'imagen': self._archivo()}, format='multipart',
        )
        self.assertEqual(response.status_code, 200)

        stub.refresh_from_db()
        self.assertTrue(stub.pendiente_revision)
