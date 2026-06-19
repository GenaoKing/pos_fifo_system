from io import StringIO
from contextlib import contextmanager
from unittest.mock import patch

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


class NormalizeImportTenantCommandTests(TestCase):
    @staticmethod
    @contextmanager
    def _fake_tenant_context(tenant):
        yield tenant

    def _summary(self, token='sync-token-plain'):
        return {
            'negocio_id': 1,
            'sucursales': 1,
            'usuarios_sin_negocio': 0,
            'ventas_sin_sucursal': 0,
            'compras_sin_sucursal': 0,
            'lotes_sin_sucursal': 0,
            'admin_username': 'admin',
            'sync_token': token,
        }

    def _call_command(self, **overrides):
        options = {
            'tenant': 'demo',
            'nombre': 'Demo',
            'slug': 'demo',
            'sucursal_codigo': '01',
            'sucursal_nombre': 'Principal',
            'admin_email': 'admin@example.com',
            'admin_password': 'Admin123!',
            'dry_run': True,
        }
        options.update(overrides)
        out = options.pop('stdout', StringIO())
        with patch(
            'apps.tenancy.management.commands.normalizar_import_tenant.tenant_context',
            self._fake_tenant_context,
        ):
            call_command('normalizar_import_tenant', stdout=out, **options)
        return out

    @patch('apps.tenancy.management.commands.normalizar_import_tenant.Command._normalize_tenant_db')
    def test_reusing_admin_email_in_other_tenant_fails_before_tenant_db(self, normalize):
        tenant = Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        other = Tenant.objects.create(tenant_key='demo2', slug='demo2', nombre='Demo 2')
        identity = Identity.objects.create(email='admin@example.com')
        Membership.objects.create(identity=identity, tenant=other, username='admin')

        with self.assertRaisesMessage(CommandError, 'ya tiene una membresia activa'):
            self._call_command(tenant=tenant.tenant_key, admin_email=' Admin@Example.com ')

        normalize.assert_not_called()

    @patch('apps.tenancy.management.commands.normalizar_import_tenant.Command._normalize_tenant_db')
    def test_reusing_admin_email_in_same_tenant_is_allowed(self, normalize):
        tenant = Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        identity = Identity.objects.create(email='admin@example.com')
        Membership.objects.create(identity=identity, tenant=tenant, username='admin')
        normalize.return_value = self._summary()

        out = self._call_command(admin_email=' Admin@Example.com ')

        normalize.assert_called_once()
        self.assertIn('DRY-RUN', out.getvalue())

    @patch('apps.tenancy.management.commands.normalizar_import_tenant.Command._normalize_tenant_db')
    def test_sync_token_is_masked_by_default(self, normalize):
        Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        normalize.return_value = self._summary(token='plain-sync-token')

        out = self._call_command()

        self.assertIn('sync_token: plain-sy...', out.getvalue())
        self.assertNotIn('plain-sync-token', out.getvalue())

    @patch('apps.tenancy.management.commands.normalizar_import_tenant.Command._normalize_tenant_db')
    def test_show_sync_token_prints_plain_token_when_requested(self, normalize):
        Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        normalize.return_value = self._summary(token='plain-sync-token')

        out = self._call_command(show_sync_token=True)

        self.assertIn('sync_token: plain-sync-token', out.getvalue())
