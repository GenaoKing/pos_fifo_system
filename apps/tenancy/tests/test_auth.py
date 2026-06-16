from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.tenancy.models import Identity, Membership, Tenant


@override_settings(TENANCY_DB_PER_TENANT_ENABLED=True)
class TenantIdentityLoginTests(TestCase):
    url = '/api/v1/auth/login/'

    def setUp(self):
        self.client = APIClient()

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
