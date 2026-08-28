from django.contrib.contenttypes.models import ContentType
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from rest_framework.request import Request

from apps.auditoria.middleware import AuditoriaMiddleware
from apps.productos.models import Producto
from apps.tenancy.context import (
    TenantContextError,
    bind_tenant_context_to_request,
    clear_current_tenant,
    get_current_tenant_alias,
    reset_current_tenant,
    set_current_tenant,
)
from apps.tenancy.middleware import ClearTenantContextMiddleware
from apps.tenancy.models import Tenant
from apps.tenancy.router import TenantDatabaseRouter
from apps.usuarios.models import Usuario


@override_settings(TENANCY_DB_PER_TENANT_ENABLED=True)
class TenantDatabaseRouterTests(SimpleTestCase):
    def setUp(self):
        self.router = TenantDatabaseRouter()

    def test_control_plane_model_routes_to_default(self):
        self.assertEqual(self.router.db_for_read(Tenant), 'default')
        self.assertEqual(self.router.db_for_write(Tenant), 'default')

    def test_dual_home_models_route_to_default_without_context(self):
        self.assertEqual(self.router.db_for_read(Usuario), 'default')
        self.assertEqual(self.router.db_for_write(ContentType), 'default')

    def test_tenant_model_without_context_fails_fast(self):
        with self.assertRaises(TenantContextError):
            self.router.db_for_read(Producto)

    def test_tenant_model_with_context_routes_to_active_alias(self):
        tokens = set_current_tenant('demo', 'tnt_demo')
        try:
            self.assertEqual(self.router.db_for_read(Producto), 'tnt_demo')
            self.assertEqual(self.router.db_for_write(Producto), 'tnt_demo')
            self.assertEqual(self.router.db_for_read(Usuario), 'tnt_demo')
            self.assertEqual(self.router.db_for_write(ContentType), 'tnt_demo')
        finally:
            reset_current_tenant(tokens)

    def test_migration_routing_keeps_control_plane_out_of_tenant_db(self):
        self.assertTrue(self.router.allow_migrate('default', 'tenancy'))
        self.assertFalse(self.router.allow_migrate('tnt_demo', 'tenancy'))
        self.assertFalse(self.router.allow_migrate('default', 'productos'))
        self.assertTrue(self.router.allow_migrate('tnt_demo', 'productos'))
        self.assertTrue(self.router.allow_migrate('default', 'usuarios'))
        self.assertTrue(self.router.allow_migrate('tnt_demo', 'usuarios'))
        self.assertTrue(self.router.allow_migrate('default', 'contenttypes'))
        self.assertTrue(self.router.allow_migrate('tnt_demo', 'contenttypes'))
        self.assertTrue(self.router.allow_migrate('default', 'admin'))
        self.assertFalse(self.router.allow_migrate('tnt_demo', 'admin'))


@override_settings(TENANCY_DB_PER_TENANT_ENABLED=True)
class TenantContextMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        clear_current_tenant()

    def tearDown(self):
        clear_current_tenant()

    def test_middleware_clears_stale_context_before_request(self):
        set_current_tenant('demo', 'tnt_demo')

        def view(request):
            self.assertIsNone(get_current_tenant_alias())
            return HttpResponse('ok')

        response = ClearTenantContextMiddleware(view)(self.factory.get('/health/'))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(get_current_tenant_alias())

    def test_middleware_clears_context_when_view_raises(self):
        def view(request):
            set_current_tenant('demo', 'tnt_demo')
            raise RuntimeError('boom')

        with self.assertRaises(RuntimeError):
            ClearTenantContextMiddleware(view)(self.factory.get('/boom/'))

        self.assertIsNone(get_current_tenant_alias())

    def test_bind_context_tokens_to_drf_and_django_requests(self):
        django_request = self.factory.get('/api/v1/auth/me/')
        drf_request = Request(django_request)
        tokens = set_current_tenant('demo', 'tnt_demo')

        try:
            bind_tenant_context_to_request(drf_request, tokens)

            self.assertIs(drf_request._tenant_context_tokens, tokens)
            self.assertIs(django_request._tenant_context_tokens, tokens)
        finally:
            reset_current_tenant(tokens)

    def test_auditoria_middleware_prepara_contexto_tambien_en_la_api(self):
        """
        AUD-005. Este test afirmaba lo contrario: que bajo tenancy el
        middleware descartaba TODO `/api/` antes de crear contexto. La API es
        justamente donde viven sync, el CRUD cloud y las operaciones
        administrativas, asi que esa omision dejaba fuera del registro
        automatico las acciones hechas con credenciales globales, de servicio o
        por impersonacion.

        La fase de request no toca la base —solo arma un dict— asi que no hay
        motivo para saltarla. Lo que si depende del tenant es la ESCRITURA, y
        eso se decide aparte, en `_sin_destino_de_escritura`.
        """
        request = self.factory.post('/api/v1/maestros/productos/')

        result = AuditoriaMiddleware(lambda req: HttpResponse('ok')).process_request(request)

        self.assertIsNone(result)
        self.assertTrue(hasattr(request, 'audit_info'))
        self.assertEqual(request.audit_info['method'], 'POST')

    def test_auditoria_no_escribe_sin_tenant_activo(self):
        """
        El limite real: con tenancy encendida y sin tenant en contexto, el
        router rechaza cualquier consulta. Ahi si hay que abstenerse.
        """
        middleware = AuditoriaMiddleware(lambda req: HttpResponse('ok'))
        request = self.factory.post('/api/v1/maestros/productos/')

        self.assertTrue(middleware._sin_destino_de_escritura(request))
