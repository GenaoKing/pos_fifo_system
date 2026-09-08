"""
BUG-G (docs/BUGS.md): un producto stub (nacido de una venta con SKU
desconocido, ver apps.api.views.sync._resolver_productos_venta) no debe
bajar por el pull hacia la sucursal que lo origino hasta que alguien lo
complete desde el portal -- si bajara pobre (categoria generica, sin
descripcion/marca), pisaria los datos reales que esa sucursal ya tiene para
el mismo SKU.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal

User = get_user_model()


class ProductoStubAntiClobberTests(TestCase):
    productos_url = '/api/v1/maestros/productos/'

    def setUp(self):
        self.categoria = Categoria.objects.create(nombre='Vasos')
        self.categoria_generica = Categoria.get_sin_clasificar()

        self.admin = User.objects.create_user(
            username='admin_stub', email='admin_stub@test.local',
            password='x', rol='ADMIN', activo=True,
        )
        self.sucursal_user = User.objects.create_user(
            username='svc_stub', email='svc_stub@test.local',
            password='x', rol='CAJERA', activo=True,
        )
        self.sucursal = Sucursal.objects.create(
            codigo='ST-001', nombre='Sucursal Stub', activa=True,
            usuario_servicio=self.sucursal_user,
        )
        self.sucursal_token = Token.objects.create(user=self.sucursal_user)

        self.normal = Producto.objects.create(
            sku='NORMAL-001', nombre='Producto normal', categoria=self.categoria,
            precio_venta='50.00',
        )
        self.stub = Producto.objects.create(
            sku='STUB-001', nombre='STUB-001', categoria=self.categoria_generica,
            precio_venta='0.00', origen_sucursal=self.sucursal, pendiente_revision=True,
        )

    def api(self, user=None, token=None):
        client = APIClient()
        if token:
            client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        elif user:
            client.force_authenticate(user=user)
        return client

    def test_token_de_sucursal_no_ve_el_stub_en_la_lista(self):
        response = self.api(token=self.sucursal_token).get(self.productos_url)

        self.assertEqual(response.status_code, 200)
        skus = {p['sku'] for p in response.data['results']}
        self.assertIn('NORMAL-001', skus)
        self.assertNotIn('STUB-001', skus)

    def test_token_de_sucursal_no_puede_leer_el_stub_por_id(self):
        response = self.api(token=self.sucursal_token).get(f'{self.productos_url}{self.stub.id}/')
        self.assertEqual(response.status_code, 404)

    def test_admin_si_ve_el_stub(self):
        response = self.api(user=self.admin).get(self.productos_url)

        self.assertEqual(response.status_code, 200)
        skus = {p['sku'] for p in response.data['results']}
        self.assertIn('STUB-001', skus)

    def test_admin_ve_el_flag_y_la_sucursal_de_origen(self):
        response = self.api(user=self.admin).get(f'{self.productos_url}{self.stub.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['pendiente_revision'])
        self.assertEqual(response.data['origen_sucursal_nombre'], 'Sucursal Stub')

    def test_filtro_pendiente_revision(self):
        respuesta_pendientes = self.api(user=self.admin).get(
            self.productos_url, {'pendiente_revision': 'true'},
        )
        self.assertEqual(
            {p['sku'] for p in respuesta_pendientes.data['results']}, {'STUB-001'},
        )

        respuesta_resto = self.api(user=self.admin).get(
            self.productos_url, {'pendiente_revision': 'false'},
        )
        self.assertEqual(
            {p['sku'] for p in respuesta_resto.data['results']}, {'NORMAL-001'},
        )

    def test_patch_solo_activo_no_libera_el_stub(self):
        """
        `toggleProduct` del portal manda solo {activo}. Si eso liberara el
        stub, un clic de activar/desactivar lo bajaria a la sucursal con sus
        datos genericos todavia puestos.
        """
        response = self.api(user=self.admin).patch(
            f'{self.productos_url}{self.stub.id}/', {'activo': False}, format='json',
        )
        self.assertEqual(response.status_code, 200)

        self.stub.refresh_from_db()
        self.assertTrue(self.stub.pendiente_revision)

        respuesta_sucursal = self.api(token=self.sucursal_token).get(self.productos_url)
        self.assertNotIn(
            'STUB-001', {p['sku'] for p in respuesta_sucursal.data['results']},
        )

    def test_patch_con_categoria_libera_el_stub(self):
        """El submit real del modal de edicion siempre manda categoria."""
        response = self.api(user=self.admin).patch(
            f'{self.productos_url}{self.stub.id}/',
            {
                'nombre': 'Producto completado', 'categoria': self.categoria.id,
                'precio_venta': '75.00',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['pendiente_revision'])

        self.stub.refresh_from_db()
        self.assertFalse(self.stub.pendiente_revision)
        self.assertEqual(self.stub.categoria_id, self.categoria.id)

        respuesta_sucursal = self.api(token=self.sucursal_token).get(self.productos_url)
        self.assertIn(
            'STUB-001', {p['sku'] for p in respuesta_sucursal.data['results']},
        )

    def test_producto_normal_no_se_ve_afectado_por_la_logica_de_categoria(self):
        """Un producto que NUNCA estuvo pendiente no cambia nada al recibir categoria."""
        response = self.api(user=self.admin).patch(
            f'{self.productos_url}{self.normal.id}/',
            {'nombre': 'Producto normal', 'categoria': self.categoria.id, 'precio_venta': '60.00'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['pendiente_revision'])
