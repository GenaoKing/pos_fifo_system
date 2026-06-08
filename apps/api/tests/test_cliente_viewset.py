from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.sucursales.models import Sucursal


class ClienteViewSetPermissionTests(TestCase):
    clientes_url = '/api/v1/maestros/clientes/'

    def setUp(self):
        self.cliente = Cliente.objects.create(
            tipo='CORPORATIVO',
            nombre='Royal Plast SRL',
            cedula_rnc='130123456',
            telefono='809-555-0001',
            activo=True,
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
        response = self.api().get(self.clientes_url)

        self.assertIn(response.status_code, (401, 403))

    def test_sucursal_puede_leer_clientes(self):
        response = self.api(token=self.sucursal_token).get(self.clientes_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'][0]['nombre'], 'Royal Plast SRL')

    def test_admin_puede_leer_clientes(self):
        response = self.api(user=self.admin).get(self.clientes_url)

        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.data['count'], 0)

    def test_respuesta_incluye_campo_es_contado(self):
        response = self.api(user=self.admin).get(self.clientes_url)

        self.assertIn('es_contado', response.data['results'][0])
        self.assertFalse(response.data['results'][0]['es_contado'])

    # --- Protección de escritura ---

    def test_sucursal_no_puede_crear_editar_ni_borrar(self):
        client = self.api(token=self.sucursal_token)

        create_r = client.post(
            self.clientes_url,
            {'tipo': 'PERSONAL', 'nombre': 'Desde sucursal'},
            format='json',
        )
        patch_r = client.patch(
            f'{self.clientes_url}{self.cliente.id}/',
            {'nombre': 'Modificado'},
            format='json',
        )
        delete_r = client.delete(f'{self.clientes_url}{self.cliente.id}/')

        self.assertEqual(create_r.status_code, 403)
        self.assertEqual(patch_r.status_code, 403)
        self.assertEqual(delete_r.status_code, 403)

    def test_cajera_no_puede_escribir(self):
        response = self.api(user=self.cajera).post(
            self.clientes_url,
            {'tipo': 'PERSONAL', 'nombre': 'Test'},
            format='json',
        )

        self.assertEqual(response.status_code, 403)

    # --- CRUD para admin ---

    def test_admin_puede_crear_cliente_personal(self):
        response = self.api(user=self.admin).post(
            self.clientes_url,
            {
                'tipo': 'PERSONAL',
                'nombre': 'Juan Pérez',
                'cedula_rnc': '40212345678',
                'telefono': '809-555-9999',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['nombre'], 'Juan Pérez')
        self.assertEqual(response.data['tipo'], 'PERSONAL')
        self.assertFalse(response.data['es_contado'])
        self.assertTrue(Cliente.objects.filter(cedula_rnc='40212345678').exists())

    def test_sysadmin_puede_editar_cliente(self):
        response = self.api(user=self.sysadmin).patch(
            f'{self.clientes_url}{self.cliente.id}/',
            {'telefono': '809-999-0000'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['telefono'], '809-999-0000')
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.telefono, '809-999-0000')

    def test_admin_puede_desactivar_cliente(self):
        response = self.api(user=self.admin).patch(
            f'{self.clientes_url}{self.cliente.id}/',
            {'activo': False},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['activo'])

    def test_admin_puede_borrar_cliente(self):
        nuevo = Cliente.objects.create(
            tipo='PERSONAL', nombre='Para borrar', activo=True
        )
        response = self.api(user=self.admin).delete(
            f'{self.clientes_url}{nuevo.id}/'
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Cliente.objects.filter(id=nuevo.id).exists())

    # --- Validaciones del serializer de escritura ---

    def test_nombre_vacio_da_error(self):
        response = self.api(user=self.admin).post(
            self.clientes_url,
            {'tipo': 'PERSONAL', 'nombre': '   '},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('nombre', response.data)

    def test_tipo_contado_no_permitido_desde_portal(self):
        response = self.api(user=self.admin).post(
            self.clientes_url,
            {'tipo': 'CONTADO', 'nombre': 'Contado Manual'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('tipo', response.data)

    def test_limite_credito_negativo_da_error(self):
        response = self.api(user=self.admin).patch(
            f'{self.clientes_url}{self.cliente.id}/',
            {'limite_credito': '-100.00'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('limite_credito', response.data)

    def test_cedula_rnc_duplicada_da_error(self):
        response = self.api(user=self.admin).post(
            self.clientes_url,
            {'tipo': 'CORPORATIVO', 'nombre': 'Duplicado SRL', 'cedula_rnc': '130123456'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)

    # --- Filtros ---

    def test_filtro_tipo(self):
        Cliente.objects.create(tipo='PERSONAL', nombre='Ana García', activo=True)
        response = self.api(user=self.admin).get(
            f'{self.clientes_url}?tipo=PERSONAL'
        )

        self.assertEqual(response.status_code, 200)
        for result in response.data['results']:
            self.assertEqual(result['tipo'], 'PERSONAL')

    def test_filtro_activo(self):
        Cliente.objects.create(tipo='PERSONAL', nombre='Inactivo', activo=False)
        response = self.api(user=self.admin).get(
            f'{self.clientes_url}?activo=false'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['nombre'], 'Inactivo')

    def test_filtro_search_por_nombre(self):
        response = self.api(user=self.admin).get(
            f'{self.clientes_url}?search=royal'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['nombre'], 'Royal Plast SRL')

    def test_filtro_search_por_cedula_rnc(self):
        response = self.api(user=self.admin).get(
            f'{self.clientes_url}?search=130123456'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['cedula_rnc'], '130123456')

    # --- Sync incremental (B11) ---

    def test_sync_incremental_desde_filtra_por_fecha_modificacion(self):
        import datetime
        from django.utils import timezone
        from urllib.parse import quote

        pasado = timezone.now() - datetime.timedelta(days=1)
        response = self.api(token=self.sucursal_token).get(
            f'{self.clientes_url}?desde={quote(pasado.isoformat())}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)

    def test_sync_incremental_no_retorna_registros_anteriores_al_cursor(self):
        import datetime
        from django.utils import timezone
        from urllib.parse import quote

        futuro = timezone.now() + datetime.timedelta(hours=1)
        response = self.api(token=self.sucursal_token).get(
            f'{self.clientes_url}?desde={quote(futuro.isoformat())}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)

    def test_edit_desde_portal_actualiza_fecha_modificacion(self):
        """
        Admin edita → fecha_modificacion cambia → sucursal lo captura
        en el próximo pull incremental.
        """
        from django.utils import timezone
        from urllib.parse import quote

        antes = timezone.now()
        self.api(user=self.admin).patch(
            f'{self.clientes_url}{self.cliente.id}/',
            {'telefono': '000-000-0000'},
            format='json',
        )
        self.cliente.refresh_from_db()

        self.assertGreaterEqual(self.cliente.fecha_modificacion, antes)

        response = self.api(token=self.sucursal_token).get(
            f'{self.clientes_url}?desde={quote(antes.isoformat())}'
        )

        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], self.cliente.id)
