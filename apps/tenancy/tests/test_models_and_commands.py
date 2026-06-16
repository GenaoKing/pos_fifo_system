from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.tenancy.models import SyncToken, Tenant


class TenancyModelTests(TestCase):
    def test_tenant_defaults_are_derived_from_tenant_key(self):
        tenant = Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        self.assertEqual(tenant.db_name, 'tnt_demo')
        self.assertEqual(tenant.media_prefix, 'demo/')

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
