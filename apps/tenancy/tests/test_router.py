from django.test import SimpleTestCase, override_settings

from apps.productos.models import Producto
from apps.tenancy.context import TenantContextError, reset_current_tenant, set_current_tenant
from apps.tenancy.models import Tenant
from apps.tenancy.router import TenantDatabaseRouter


@override_settings(TENANCY_DB_PER_TENANT_ENABLED=True)
class TenantDatabaseRouterTests(SimpleTestCase):
    def setUp(self):
        self.router = TenantDatabaseRouter()

    def test_control_plane_model_routes_to_default(self):
        self.assertEqual(self.router.db_for_read(Tenant), 'default')
        self.assertEqual(self.router.db_for_write(Tenant), 'default')

    def test_tenant_model_without_context_fails_fast(self):
        with self.assertRaises(TenantContextError):
            self.router.db_for_read(Producto)

    def test_tenant_model_with_context_routes_to_active_alias(self):
        tokens = set_current_tenant('demo', 'tnt_demo')
        try:
            self.assertEqual(self.router.db_for_read(Producto), 'tnt_demo')
            self.assertEqual(self.router.db_for_write(Producto), 'tnt_demo')
        finally:
            reset_current_tenant(tokens)

    def test_migration_routing_keeps_control_plane_out_of_tenant_db(self):
        self.assertTrue(self.router.allow_migrate('default', 'tenancy'))
        self.assertFalse(self.router.allow_migrate('tnt_demo', 'tenancy'))
        self.assertFalse(self.router.allow_migrate('default', 'productos'))
        self.assertTrue(self.router.allow_migrate('tnt_demo', 'productos'))
