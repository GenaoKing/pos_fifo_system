from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory

from apps.api.authentication import SucursalTokenAuthentication
from apps.api.permissions import TienePermiso
from apps.tenancy.authentication import TenantJWTAuthentication
from apps.tenancy.context import clear_current_tenant
from apps.tenancy.models import Identity, Membership, SyncToken, Tenant


@override_settings(TENANCY_DB_PER_TENANT_ENABLED=True)
class TenantIdentityLoginTests(TestCase):
    url = '/api/v1/auth/login/'

    def setUp(self):
        self.client = APIClient()

    def tearDown(self):
        clear_current_tenant()

    def _identity(self, email='root@example.com', password='x', **kwargs):
        identity = Identity(email=email, **kwargs)
        identity.set_password(password)
        identity.save()
        return identity

    def test_global_identity_can_login_without_tenant_membership(self):
        self._identity(is_global=True, nombre='Root')

        response = self.client.post(
            self.url,
            {'email': 'root@example.com', 'password': 'x'},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertTrue(response.data['user']['is_global'])
        self.assertIsNone(response.data['user']['tenant_id'])

    def test_tenant_jwt_authentication_accepts_global_identity_token(self):
        self._identity(is_global=True, nombre='Root')
        login = self.client.post(
            self.url,
            {'email': 'root@example.com', 'password': 'x'},
            format='json',
        )
        self.assertEqual(login.status_code, 200, login.data)

        request = APIRequestFactory().get(
            '/api/v1/auth/me/',
            HTTP_AUTHORIZATION=f'Bearer {login.data["access"]}',
        )
        user, token = TenantJWTAuthentication().authenticate(request)

        self.assertTrue(user.is_global_identity)
        self.assertEqual(user.email, 'root@example.com')
        self.assertTrue(token['is_global'])

    def test_invalid_password_is_rejected(self):
        self._identity(is_global=True)

        response = self.client.post(
            self.url,
            {'email': 'root@example.com', 'password': 'bad'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)

    def test_inactive_identity_is_rejected(self):
        self._identity(is_global=True, activo=False)

        response = self.client.post(
            self.url,
            {'email': 'root@example.com', 'password': 'x'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)

    def test_multiple_memberships_are_rejected_in_mvp_before_tenant_db_access(self):
        identity = self._identity(email='owner@example.com')
        tenant_a = Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        tenant_b = Tenant.objects.create(tenant_key='demo2', slug='demo2', nombre='Demo 2')
        Membership.objects.create(identity=identity, tenant=tenant_a, username='admin')
        Membership.objects.create(identity=identity, tenant=tenant_b, username='admin')

        response = self.client.post(
            self.url,
            {'email': 'owner@example.com', 'password': 'x'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('exactamente un tenant', str(response.data))

    def test_tenant_jwt_authentication_is_registered_in_base_settings(self):
        self.assertEqual(
            settings.REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES'][0],
            'apps.tenancy.authentication.TenantJWTAuthentication',
        )

    def test_sucursal_token_auth_sets_sucursal_on_drf_and_django_request(self):
        tenant = Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        token = 'plain-token'
        SyncToken.objects.create(
            tenant=tenant,
            token_hash=SyncToken.hash_token(token),
            sucursal_codigo='SD-001',
        )
        django_request = APIRequestFactory().get('/api/v1/sync/pull/')
        drf_request = Request(django_request)
        fake_user = SimpleNamespace(is_authenticated=True)
        fake_token = SimpleNamespace()
        fake_sucursal = SimpleNamespace(codigo='SD-001', activa=True)

        auth = SucursalTokenAuthentication()
        with patch(
            'apps.api.authentication.configure_tenant_database',
            return_value=(tenant, 'tnt_demo'),
        ), patch(
            'rest_framework.authentication.TokenAuthentication.authenticate_credentials',
            return_value=(fake_user, fake_token),
        ), patch.object(
            SucursalTokenAuthentication,
            '_attach_sucursal',
            return_value=(fake_user, fake_token),
        ):
            fake_token.sucursal = fake_sucursal
            user, auth_token = auth.authenticate_credentials_for_tenant(token, drf_request)

        self.assertIs(user, fake_user)
        self.assertIs(auth_token, fake_token)
        self.assertIs(drf_request.sucursal, fake_sucursal)
        self.assertIs(django_request.sucursal, fake_sucursal)
        self.assertTrue(hasattr(drf_request, '_tenant_context_tokens'))
        self.assertIs(
            django_request._tenant_context_tokens,
            drf_request._tenant_context_tokens,
        )

    def test_tiene_permiso_uses_auth_sucursal_fallback(self):
        fake_sucursal = SimpleNamespace(codigo='SD-001')
        captured = {}

        class User:
            is_authenticated = True

            def tiene_permiso(self, codigo, sucursal=None):
                captured['codigo'] = codigo
                captured['sucursal'] = sucursal
                return True

        class RequiereClientesVer(TienePermiso):
            codigo = 'clientes.ver'

        request = SimpleNamespace(
            user=User(),
            auth=SimpleNamespace(sucursal=fake_sucursal),
            sucursal=None,
        )

        self.assertTrue(RequiereClientesVer().has_permission(request, None))
        self.assertEqual(captured['codigo'], 'clientes.ver')
        self.assertIs(captured['sucursal'], fake_sucursal)
