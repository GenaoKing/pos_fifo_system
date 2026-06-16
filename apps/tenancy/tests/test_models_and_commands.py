from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.tenancy.models import Identity, Membership, SyncToken, Tenant


class TenancyModelTests(TestCase):
    def test_tenant_defaults_are_derived_from_tenant_key(self):
        tenant = Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        self.assertEqual(tenant.db_name, 'tnt_demo')
        self.assertEqual(tenant.media_prefix, 'demo/')

    def test_tenant_slug_is_unique_when_derived_from_name(self):
        first = Tenant.objects.create(tenant_key='demo', nombre='Mi Empresa')
        second = Tenant.objects.create(tenant_key='demo2', nombre='Mi Empresa')

        self.assertEqual(first.slug, 'mi-empresa')
        self.assertNotEqual(second.slug, first.slug)
        self.assertTrue(second.slug.startswith('mi-empresa'))

    def test_sync_token_hash_is_stable_and_does_not_store_plain_token(self):
        token = 'secret-token'
        digest = SyncToken.hash_token(token)
        self.assertEqual(digest, SyncToken.hash_token(token))
        self.assertNotIn(token, digest)
        self.assertEqual(len(digest), 64)


class BootstrapTenantDryRunTests(TestCase):
    def test_dry_run_does_not_create_control_plane_rows(self):
        out = StringIO()
        call_command(
            'bootstrap_tenant',
            tenant='demo',
            nombre='Demo Tenant',
            admin_email='admin@demo.local',
            dry_run=True,
            stdout=out,
        )
        self.assertIn('DRY-RUN', out.getvalue())
        self.assertEqual(Tenant.objects.count(), 0)

    def test_explicit_duplicate_slug_fails_fast(self):
        Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')

        with self.assertRaisesMessage(CommandError, 'ya pertenece'):
            call_command(
                'bootstrap_tenant',
                tenant='demo2',
                nombre='Demo 2',
                slug='demo',
                dry_run=True,
            )

    def test_reusing_admin_email_in_other_tenant_fails_fast(self):
        tenant = Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        other = Tenant.objects.create(tenant_key='demo2', slug='demo2', nombre='Demo 2')
        identity = Identity.objects.create(email='admin@example.com')
        Membership.objects.create(identity=identity, tenant=tenant, username='admin')

        with self.assertRaisesMessage(CommandError, 'ya tiene una membresia activa'):
            call_command(
                'bootstrap_tenant',
                tenant=other.tenant_key,
                nombre='Demo 2',
                admin_email='admin@example.com',
                dry_run=True,
            )

    def test_reusing_admin_email_in_same_tenant_is_allowed(self):
        tenant = Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        identity = Identity.objects.create(email='admin@example.com')
        Membership.objects.create(identity=identity, tenant=tenant, username='admin')

        out = StringIO()
        call_command(
            'bootstrap_tenant',
            tenant='demo',
            nombre='Demo',
            admin_email='admin@example.com',
            dry_run=True,
            stdout=out,
        )

        self.assertIn('DRY-RUN', out.getvalue())
