"""
Gating de modulo en la API: el endpoint de cuentas por cobrar solo responde si el
negocio del usuario tiene el modulo 'cuentas_por_cobrar' en su plan.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.negocios.models import Negocio
from apps.suscripciones import seed
from apps.suscripciones.models import Modulo, Plan, SuscripcionNegocio

User = get_user_model()


class CxcModuloGatingTests(TestCase):
    url = '/api/v1/cuentas-por-cobrar/'

    def setUp(self):
        seed.sembrar_modulos(Modulo)
        seed.crear_planes_default(Plan, Modulo)

        self.neg_con = Negocio.objects.create(nombre='Con CxC', slug='con-cxc')
        self.neg_sin = Negocio.objects.create(nombre='Sin CxC', slug='sin-cxc')
        SuscripcionNegocio.objects.create(
            negocio=self.neg_con, plan=Plan.objects.get(slug='empresarial'), activa=True
        )
        SuscripcionNegocio.objects.create(
            negocio=self.neg_sin, plan=Plan.objects.get(slug='basico'), activa=True
        )

        self.user_con = User.objects.create_user(
            'admin_con', 'c@e.com', 'x', rol='ADMIN', negocio=self.neg_con
        )
        self.user_sin = User.objects.create_user(
            'admin_sin', 's@e.com', 'x', rol='ADMIN', negocio=self.neg_sin
        )

    def _api(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_negocio_con_modulo_puede_leer(self):
        self.assertEqual(self._api(self.user_con).get(self.url).status_code, 200)

    def test_negocio_sin_modulo_403(self):
        self.assertEqual(self._api(self.user_sin).get(self.url).status_code, 403)
