"""
BUG-E (endurecimiento relacionado): las rutas de TEMPLATE del POS local
(`/login/`, `/reportes/`, etc.) no deben existir bajo DB-per-tenant. Sin
tenant activo, `config_negocio` (context processor de cada render) revienta
con `TenantContextError` -> 500 -- exactamente el ruido que scanners y bots
dejaban en los logs de produccion.

`config/urls.py` calcula que patrones incluir UNA VEZ, al importarse el
modulo (via `settings.TENANCY_DB_PER_TENANT_ENABLED`). Probarlo exige
recargar ese modulo con el flag en cada valor -- no hay forma de hacerlo
"por request" porque no lo es.
"""
import importlib

from django.test import TestCase, override_settings
from django.urls import clear_url_caches


def _recargar_urls(tenancy_enabled):
    import config.urls as urls_mod

    with override_settings(TENANCY_DB_PER_TENANT_ENABLED=tenancy_enabled):
        importlib.reload(urls_mod)
    clear_url_caches()


class RutasLocalesOcultasBajoTenancyTests(TestCase):
    def setUp(self):
        # Restaurar SIEMPRE al estado real de settings_development (False),
        # se haya recargado a True o no, y aunque el test falle a mitad.
        self.addCleanup(_recargar_urls, False)

    def test_rutas_de_template_local_no_existen_bajo_tenancy(self):
        _recargar_urls(True)

        for ruta in ('/login/', '/reportes/', '/pos/', '/productos/', '/caja/'):
            with self.subTest(ruta=ruta):
                resp = self.client.get(ruta)
                self.assertEqual(
                    resp.status_code, 404,
                    f'{ruta} deberia no existir bajo DB-per-tenant (evita el 500 de BUG-E)',
                )

    def test_api_y_admin_y_health_siguen_disponibles_bajo_tenancy(self):
        _recargar_urls(True)

        resp = self.client.get('/api/v1/health/')
        self.assertEqual(resp.status_code, 200)

        resp_admin = self.client.get('/admin/login/')
        self.assertNotEqual(resp_admin.status_code, 404)

    def test_las_mismas_rutas_siguen_existiendo_sin_tenancy(self):
        """
        La instalacion local (el caso normal, sin DB-per-tenant) no debe
        perder ninguna ruta por este cambio.
        """
        _recargar_urls(False)

        resp = self.client.get('/login/')
        self.assertNotEqual(resp.status_code, 404)
