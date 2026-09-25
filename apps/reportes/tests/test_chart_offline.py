"""
apps/reportes/tests/test_chart_offline.py

REPORTES-CHART-CDN (docs/handoffs/cierre_prod/INVENTARIO.md) — la pagina de
reportes on-demand cargaba Chart.js desde jsdelivr. En un POS sin Internet
estable los graficos no renderizan aunque los datos ya esten en pantalla.

Reproduccion de mutacion: revertir el `{% static %}` de
templates/reportes/on_demand.html al `<script src="https://cdn...">` original
hace fallar `test_no_referencia_cdn_externo`.
"""
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.templatetags.static import static
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.configuracion.models import ConfiguracionNegocio
from apps.negocios.models import Negocio
from apps.permisos.testing import asignar, crear_rol
from apps.sucursales.models import Sucursal


@override_settings(SUCURSAL_CODIGO='SUC-A')
class ChartJsOfflineTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()

        self.negocio = Negocio.objects.create(nombre='Rep Chart', slug='rep-chart')
        self.suc_a = Sucursal.objects.create(
            negocio=self.negocio, codigo='SUC-A', nombre='Sucursal A',
        )
        ConfiguracionNegocio.objects.create(sucursal=self.suc_a)

        self.user = User.objects.create_user(
            username='rep_chart', email='rep_chart@t.local', password='x',
            rol='CAJERA', activo=True,
        )
        asignar(
            self.user,
            crear_rol(self.negocio, 'Ver Reportes', ['reportes.consolidado.ver']),
            sucursal=None,
        )
        cache.clear()
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _get_on_demand(self):
        resp = self.client.get(reverse('reportes:on_demand'))
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_no_referencia_cdn_externo(self):
        contenido = self._get_on_demand()
        self.assertNotIn('cdn.jsdelivr.net', contenido)
        self.assertNotIn('https://cdn', contenido)

    def test_referencia_asset_local_de_chart_js(self):
        contenido = self._get_on_demand()
        url_esperada = static('js/chart.min.js')
        self.assertIn(f'<script src="{url_esperada}"></script>', contenido)

    def test_asset_local_de_chart_js_resuelve_en_staticfiles(self):
        # No alcanza con que la plantilla mencione la ruta: confirma que
        # staticfiles realmente puede servirla (dev finder o STATIC_ROOT tras
        # collectstatic).
        ruta = finders.find('js/chart.min.js')
        self.assertIsNotNone(
            ruta,
            'js/chart.min.js no lo encuentra ningun finder de staticfiles',
        )
