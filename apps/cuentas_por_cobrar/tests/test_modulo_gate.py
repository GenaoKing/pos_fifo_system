"""
apps/cuentas_por_cobrar/tests/test_modulo_gate.py

SUS-006 / CT-02 — gate de MÓDULO en las vistas de CxC, ortogonal al permiso.

El módulo `cuentas_por_cobrar` es vendible: si el plan del tenant no lo incluye,
las vistas HTML y las APIs responden 404 aunque el usuario tenga el permiso
`cuentas_por_cobrar.ver`. Ocultar el menú no aplicaba el contrato comercial: una
URL guardada conservaba la funcionalidad.

El módulo se apaga con una suscripción SUSPENDIDA (deja solo los módulos core),
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
class CxCModuloGateTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()

        self.negocio = Negocio.objects.create(nombre='CxC Modulo', slug='cxc-modulo')
        self.suc_a = Sucursal.objects.create(
            negocio=self.negocio, codigo='SUC-A', nombre='Sucursal A',
        )
        ConfiguracionNegocio.load(sucursal=self.suc_a)

        # Usuario CON permiso de ver CxC: prueba que es el MÓDULO —no el permiso—
        # el que deniega cuando el plan no lo incluye.
        self.user = User.objects.create_user(
            username='cxc_ver', email='cxc_ver@t.local', password='x',
            rol='CAJERA', activo=True,
        )
        asignar(
            self.user,
            crear_rol(self.negocio, 'Ver CxC', ['cuentas_por_cobrar.ver']),
            sucursal=self.suc_a,
        )
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _apagar_modulo(self):
        # Suscripción SUSPENDIDA -> solo módulos core -> cuentas_por_cobrar off.
        SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=None, activa=False,
        )
        cache.clear()

    def test_modulo_activo_permite_html(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('cuentas_por_cobrar:lista'))
        self.assertEqual(resp.status_code, 200)

    def test_modulo_apagado_denega_html(self):
        self._apagar_modulo()
        self.client.force_login(self.user)
        resp = self.client.get(reverse('cuentas_por_cobrar:lista'))
        self.assertEqual(resp.status_code, 404)

    def test_modulo_apagado_denega_api(self):
        self._apagar_modulo()
        self.client.force_login(self.user)
        resp = self.client.get(reverse('cuentas_por_cobrar:api_metodos'))
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(resp.json()['success'])
