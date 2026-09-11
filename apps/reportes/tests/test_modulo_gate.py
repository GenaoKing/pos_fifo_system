"""
apps/reportes/tests/test_modulo_gate.py

SUS-006 / CT-02 — gate de MÓDULO en reportes on-demand, ortogonal al permiso.

`reportes_ondemand` es vendible: si el plan del tenant no lo incluye, la página
y sus APIs responden 404 aunque el usuario tenga alcance de reportes. Antes la
página respondía 200 con el flag legacy apagado y el módulo fuera del plan.

El módulo se apaga con una suscripción SUSPENDIDA (deja solo módulos core),
usando el resolutor real (`apps.suscripciones.engine` vía `modulo_activo`).
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.configuracion.models import ConfiguracionNegocio
from apps.negocios.models import Negocio
from apps.permisos.testing import asignar, crear_rol
from apps.sucursales.models import Sucursal
from apps.suscripciones.models import SuscripcionNegocio


@override_settings(SUCURSAL_CODIGO='SUC-A')
class ReportesOnDemandModuloGateTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()

        self.negocio = Negocio.objects.create(nombre='Rep Modulo', slug='rep-modulo')
        self.suc_a = Sucursal.objects.create(
            negocio=self.negocio, codigo='SUC-A', nombre='Sucursal A',
        )
        ConfiguracionNegocio.load(sucursal=self.suc_a)

        # Usuario CON alcance de reportes (consolidado global): prueba que es el
        # MÓDULO —no el permiso— el que deniega cuando el plan no lo incluye.
        self.user = User.objects.create_user(
            username='rep_ver', email='rep_ver@t.local', password='x',
            rol='CAJERA', activo=True,
        )
        asignar(
            self.user,
            crear_rol(self.negocio, 'Ver Reportes', ['reportes.consolidado.ver']),
            sucursal=None,  # global -> alcance permitido y consolidado
        )
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _apagar_modulo(self):
        SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=None, activa=False,
        )
        cache.clear()

    def test_modulo_activo_permite(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('reportes:on_demand'))
        self.assertEqual(resp.status_code, 200)

    def test_modulo_apagado_denega_html(self):
        self._apagar_modulo()
        self.client.force_login(self.user)
        resp = self.client.get(reverse('reportes:on_demand'))
        self.assertEqual(resp.status_code, 404)

    def test_modulo_apagado_denega_api(self):
        self._apagar_modulo()
        self.client.force_login(self.user)
        resp = self.client.get(reverse('reportes:api_ventas_periodo'))
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(resp.json()['success'])
