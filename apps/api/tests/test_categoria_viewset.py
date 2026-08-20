from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal


class CategoriaViewSetPermissionTests(TestCase):
    categorias_url = '/api/v1/maestros/categorias/'

    def setUp(self):
        self.categoria = Categoria.objects.create(
            nombre='Envases',
            descripcion='Envases plásticos',
            activa=True,
            tipo_negocio='plasticos',
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

    # --- Lecturas ---

    def test_list_requiere_autenticacion(self):
        response = self.api().get(self.categorias_url)

        self.assertIn(response.status_code, (401, 403))

    def test_sucursal_puede_leer_categorias(self):
        response = self.api(token=self.sucursal_token).get(self.categorias_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['nombre'], 'Envases')

    def test_admin_puede_leer_categorias(self):
        response = self.api(user=self.admin).get(self.categorias_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'][0]['nombre'], 'Envases')

    def test_list_incluye_total_productos(self):
        Producto.objects.create(
            sku='P-001', nombre='Vaso 8oz',
            categoria=self.categoria, precio_venta='50.00',
        )
        response = self.api(user=self.admin).get(self.categorias_url)

        self.assertEqual(response.data['results'][0]['total_productos'], 1)

    # --- Protección de escritura para sucursales y cajeras ---

    def test_sucursal_no_puede_crear_editar_ni_borrar(self):
        client = self.api(token=self.sucursal_token)

        create_r = client.post(
            self.categorias_url,
            {'nombre': 'Desde sucursal'},
            format='json',
        )
        patch_r = client.patch(
            f'{self.categorias_url}{self.categoria.id}/',
            {'nombre': 'Modificado'},
            format='json',
        )
        delete_r = client.delete(f'{self.categorias_url}{self.categoria.id}/')

        self.assertEqual(create_r.status_code, 403)
        self.assertEqual(patch_r.status_code, 403)
        self.assertEqual(delete_r.status_code, 403)

    def test_cajera_no_puede_escribir(self):
        response = self.api(user=self.cajera).post(
            self.categorias_url,
            {'nombre': 'Nueva Categoria'},
            format='json',
        )

        self.assertEqual(response.status_code, 403)

    # --- CRUD para admin ---

    def test_admin_puede_crear_categoria(self):
        response = self.api(user=self.admin).post(
            self.categorias_url,
            {
                'nombre': 'Autopartes',
                'descripcion': 'Repuestos de vehículos',
                'tipo_negocio': 'autopartes',
                'atributos_configurados': {'marca': '', 'modelo': ''},
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['nombre'], 'Autopartes')
        self.assertEqual(response.data['tipo_negocio'], 'autopartes')
        self.assertIn('total_productos', response.data)
        self.assertTrue(Categoria.objects.filter(nombre='Autopartes').exists())

    def test_sysadmin_puede_editar_categoria(self):
        response = self.api(user=self.sysadmin).patch(
            f'{self.categorias_url}{self.categoria.id}/',
            {'descripcion': 'Descripción actualizada'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['descripcion'], 'Descripción actualizada')
        self.categoria.refresh_from_db()
        self.assertEqual(self.categoria.descripcion, 'Descripción actualizada')

    def test_admin_puede_desactivar_categoria(self):
        response = self.api(user=self.admin).patch(
            f'{self.categorias_url}{self.categoria.id}/',
            {'activa': False},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['activa'], False)

    def test_admin_puede_borrar_categoria(self):
        nueva = Categoria.objects.create(nombre='Para borrar')
        response = self.api(user=self.admin).delete(
            f'{self.categorias_url}{nueva.id}/'
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Categoria.objects.filter(id=nueva.id).exists())

    # --- Validaciones del serializer de escritura ---

    def test_nombre_vacio_da_error(self):
        response = self.api(user=self.admin).post(
            self.categorias_url,
            {'nombre': '   '},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('nombre', response.data)

    def test_atributos_configurados_no_puede_ser_array(self):
        response = self.api(user=self.admin).post(
            self.categorias_url,
            {'nombre': 'Test', 'atributos_configurados': ['a', 'b']},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('atributos_configurados', response.data)

    def test_nombre_duplicado_da_error(self):
        response = self.api(user=self.admin).post(
            self.categorias_url,
            {'nombre': 'Envases'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)

    # --- Filtros ---

    def test_filtro_activa(self):
        Categoria.objects.create(nombre='Inactiva', activa=False)
        response = self.api(user=self.admin).get(
            f'{self.categorias_url}?activa=false'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['nombre'], 'Inactiva')

    def test_filtro_search_por_nombre(self):
        Categoria.objects.create(nombre='Bolsas Plásticas')
        response = self.api(user=self.admin).get(
            f'{self.categorias_url}?search=bolsa'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['nombre'], 'Bolsas Plásticas')

    # --- Sync incremental (B11) ---

    def test_sync_incremental_desde_filtra_por_fecha_modificacion(self):
        import datetime
        from django.utils import timezone
        from urllib.parse import quote

        pasado = timezone.now() - datetime.timedelta(days=1)
        desde_str = quote(pasado.isoformat())

        response = self.api(token=self.sucursal_token).get(
            f'{self.categorias_url}?desde={desde_str}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)

    def test_sync_incremental_no_retorna_registros_anteriores_al_cursor(self):
        import datetime
        from django.utils import timezone
        from urllib.parse import quote

        futuro = timezone.now() + datetime.timedelta(hours=1)
        desde_str = quote(futuro.isoformat())

        response = self.api(token=self.sucursal_token).get(
            f'{self.categorias_url}?desde={desde_str}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)

    def test_respuesta_incluye_header_x_sync_timestamp(self):
        response = self.api(token=self.sucursal_token).get(self.categorias_url)

        self.assertIn('X-Sync-Timestamp', response)

    def test_edit_desde_portal_actualiza_fecha_modificacion(self):
        """
        Una vez que admin edita, la sucursal recibe el cambio en el próximo
        pull incremental porque fecha_modificacion se actualiza (auto_now=True).
        """
        from datetime import timedelta

        from django.utils import timezone
        from urllib.parse import quote

        # `antes` se retrasa unos ms a proposito. El filtro del cursor es
        # `fecha_modificacion__gt` (estrictamente mayor) y en Windows
        # `timezone.now()` tiene una resolucion de 15.6 ms: si el PATCH ocurre
        # dentro del mismo tick, `fecha_modificacion == antes` y el registro
        # queda fuera del rango, haciendo fallar el test sin que haya bug.
        antes = timezone.now() - timedelta(milliseconds=100)
        self.api(user=self.admin).patch(
            f'{self.categorias_url}{self.categoria.id}/',
            {'descripcion': 'Nueva descripcion'},
            format='json',
        )
        self.categoria.refresh_from_db()

        self.assertGreaterEqual(self.categoria.fecha_modificacion, antes)

        desde_str = quote(antes.isoformat())
        response = self.api(token=self.sucursal_token).get(
            f'{self.categorias_url}?desde={desde_str}'
        )

        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], self.categoria.id)
