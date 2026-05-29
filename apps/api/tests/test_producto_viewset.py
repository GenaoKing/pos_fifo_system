from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal


class ProductoViewSetPermissionTests(TestCase):
    productos_url = '/api/v1/maestros/productos/'

    def setUp(self):
        self.categoria = Categoria.objects.create(nombre='Vasos')
        self.producto = Producto.objects.create(
            sku='TEST-001',
            codigo_barras='CB-001',
            nombre='Vaso 16oz',
            descripcion='Producto inicial',
            categoria=self.categoria,
            precio_venta='100.00',
            stock_minimo=5,
            activo=True,
            estado='nuevo',
            marca='Royal',
            atributos={'color': 'transparente'},
        )

        User = get_user_model()
        self.admin = User.objects.create_user(
            username='admin_portal',
            email='admin@example.com',
            password='pass',
            rol='ADMIN',
            activo=True,
        )
        self.sysadmin = User.objects.create_user(
            username='sysadmin_portal',
            email='sysadmin@example.com',
            password='pass',
            rol='SYSADMIN',
            activo=True,
        )
        self.cajera = User.objects.create_user(
            username='cajera',
            email='cajera@example.com',
            password='pass',
            rol='CAJERA',
            activo=True,
        )
        self.sucursal_user = User.objects.create_user(
            username='svc_sd001',
            email='svc_sd001@example.com',
            password='pass',
            rol='CAJERA',
            activo=True,
        )
        self.sucursal = Sucursal.objects.create(
            codigo='SD-001',
            nombre='Sucursal SD',
            activa=True,
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

    def test_list_requiere_autenticacion(self):
        response = self.api().get(self.productos_url)

        self.assertIn(response.status_code, (401, 403))

    def test_sucursal_puede_leer_productos(self):
        response = self.api(token=self.sucursal_token).get(self.productos_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['sku'], 'TEST-001')

    def test_admin_puede_leer_productos(self):
        response = self.api(user=self.admin).get(self.productos_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'][0]['nombre'], 'Vaso 16oz')

    def test_sucursal_no_puede_crear_editar_ni_borrar(self):
        client = self.api(token=self.sucursal_token)

        create_response = client.post(
            self.productos_url,
            {
                'sku': 'TEST-002',
                'nombre': 'Producto desde sucursal',
                'precio_venta': '50.00',
                'categoria': self.categoria.id,
            },
            format='json',
        )
        patch_response = client.patch(
            f'{self.productos_url}{self.producto.id}/',
            {'precio_venta': '125.00'},
            format='json',
        )
        delete_response = client.delete(f'{self.productos_url}{self.producto.id}/')

        self.assertEqual(create_response.status_code, 403)
        self.assertEqual(patch_response.status_code, 403)
        self.assertEqual(delete_response.status_code, 403)

    def test_cajera_no_puede_escribir(self):
        response = self.api(user=self.cajera).patch(
            f'{self.productos_url}{self.producto.id}/',
            {'precio_venta': '125.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 403)

    def test_admin_puede_crear_producto(self):
        response = self.api(user=self.admin).post(
            self.productos_url,
            {
                'sku': 'TEST-002',
                'nombre': 'Producto de prueba',
                'precio_venta': '100.00',
                'categoria': self.categoria.id,
                'atributos': {'color': 'rojo'},
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['sku'], 'TEST-002')
        self.assertEqual(response.data['imagen_url'], None)
        self.assertTrue(
            Producto.objects.filter(sku='TEST-002', atributos={'color': 'rojo'}).exists()
        )

    def test_sysadmin_puede_editar_producto(self):
        response = self.api(user=self.sysadmin).patch(
            f'{self.productos_url}{self.producto.id}/',
            {'precio_venta': '125.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['precio_venta'], '125.00')

    def test_patch_ignora_cambio_de_sku_pero_aplica_otros_campos(self):
        response = self.api(user=self.admin).patch(
            f'{self.productos_url}{self.producto.id}/',
            {'sku': 'OTRO-SKU', 'nombre': 'Nombre nuevo'},
            format='json',
        )
        self.producto.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['sku'], 'TEST-001')
        self.assertEqual(self.producto.sku, 'TEST-001')
        self.assertEqual(self.producto.nombre, 'Nombre nuevo')

    def test_admin_puede_borrar_producto(self):
        response = self.api(user=self.admin).delete(
            f'{self.productos_url}{self.producto.id}/'
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Producto.objects.filter(id=self.producto.id).exists())

    def test_validaciones_del_serializer_de_escritura(self):
        client = self.api(user=self.admin)

        precio_response = client.patch(
            f'{self.productos_url}{self.producto.id}/',
            {'precio_venta': '0'},
            format='json',
        )
        atributos_response = client.patch(
            f'{self.productos_url}{self.producto.id}/',
            {'atributos': ['esto', 'es', 'array']},
            format='json',
        )

        self.assertEqual(precio_response.status_code, 400)
        self.assertEqual(
            precio_response.data['precio_venta'][0],
            'El precio debe ser mayor a cero.',
        )
        self.assertEqual(atributos_response.status_code, 400)
        self.assertIn('atributos', atributos_response.data)
