"""SUS-007/CFG-007: capacidades efectivas en el pull de configuracion."""
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.configuracion.models import ConfiguracionNegocio
from apps.negocios.models import Negocio
from apps.sucursales.models import Sucursal
from apps.suscripciones import seed
from apps.suscripciones.models import (
    Modulo,
    Plan,
    SucursalModuloOverride,
    SuscripcionNegocio,
)


User = get_user_model()


class ConfiguracionEfectivaCT03Tests(TestCase):
    def setUp(self):
        cache.clear()
        seed.sembrar_modulos(Modulo)
        seed.crear_planes_default(Plan, Modulo)
        self.negocio = Negocio.objects.create(
            nombre='Negocio CT03 Sync', slug='ct03-sync',
        )
        self.suscripcion = SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=Plan.objects.get(slug='basico'), activa=True,
        )
        self.servicio = User.objects.create_user(
            'svc_ct03_sync', 'svc-ct03-sync@example.test', 'x', rol='CAJERA',
        )
        self.sucursal = Sucursal.objects.create(
            codigo='CT03-SYNC', nombre='Sucursal CT03', activa=True,
            negocio=self.negocio, usuario_servicio=self.servicio,
        )
        self.config = ConfiguracionNegocio.objects.create(
            sucursal=self.sucursal,
            nombre_negocio='Configuracion CT03',
            # Contradice deliberadamente el plan basico: el endpoint no puede
            # exponer este flag crudo cuando hay autoridad comercial.
            modulo_cotizaciones=True,
        )
        self.token = Token.objects.create(user=self.servicio)
        self.operador = User.objects.create_user(
            'operador_ct03_sync', 'operador-ct03-sync@example.test', 'x',
            rol='SYSADMIN',
        )

    def tearDown(self):
        cache.clear()

    def _pull(self, desde=None):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        url = '/api/v1/sync/configuracion/'
        if desde:
            url = f'{url}?{urlencode({"desde": desde})}'
        return client.get(url)

    def _suscripciones(self):
        client = APIClient()
        client.force_authenticate(user=self.operador)
        return client

    def test_flags_legacy_salen_del_engine_y_plan_override_reabren_el_cursor(self):
        inicial = self._pull()
        self.assertEqual(inicial.status_code, 200)
        self.assertFalse(inicial.data[0]['modulo_cotizaciones'])
        cursor_inicial = inicial.data[0]['fecha_modificacion']
        self.assertEqual(self._pull(cursor_inicial).data, [])

        cambio_plan = self._suscripciones().patch(
            f'/api/v1/suscripciones/negocios/{self.suscripcion.id}/',
            {'plan': 'pro'}, format='json',
        )
        self.assertEqual(cambio_plan.status_code, 200, cambio_plan.data)

        tras_plan = self._pull(cursor_inicial)
        self.assertEqual(tras_plan.status_code, 200)
        self.assertTrue(tras_plan.data[0]['modulo_cotizaciones'])
        cursor_plan = tras_plan.data[0]['fecha_modificacion']
        self.assertGreater(cursor_plan, cursor_inicial)

        cambio_override = self._suscripciones().post(
            '/api/v1/suscripciones/overrides/',
            {
                'negocio': self.negocio.id,
                'modulo': 'cotizaciones',
                'incluido': False,
            },
            format='json',
        )
        self.assertEqual(cambio_override.status_code, 201, cambio_override.data)

        tras_override = self._pull(cursor_plan)
        self.assertEqual(tras_override.status_code, 200)
        self.assertFalse(tras_override.data[0]['modulo_cotizaciones'])
        self.assertGreater(
            tras_override.data[0]['fecha_modificacion'], cursor_plan,
        )

    def test_sin_negocio_preserva_el_flag_legacy_crudo(self):
        sucursal_legacy = Sucursal.objects.create(
            codigo='CT03-LEGACY', nombre='Sucursal legacy', activa=True,
        )
        servicio_legacy = User.objects.create_user(
            'svc_ct03_legacy', 'svc-ct03-legacy@example.test', 'x', rol='CAJERA',
        )
        sucursal_legacy.usuario_servicio = servicio_legacy
        sucursal_legacy.save(update_fields=['usuario_servicio'])
        ConfiguracionNegocio.objects.create(
            sucursal=sucursal_legacy,
            nombre_negocio='Legacy', modulo_cotizaciones=False,
        )
        token_legacy = Token.objects.create(user=servicio_legacy)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token_legacy.key}')

        response = client.get('/api/v1/sync/configuracion/')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data[0]['modulo_cotizaciones'])

    def test_override_de_sucursal_participa_en_el_flag_legacy_efectivo(self):
        self.suscripcion.plan = Plan.objects.get(slug='pro')
        self.suscripcion.save()
        SucursalModuloOverride.objects.create(
            sucursal=self.sucursal,
            modulo=Modulo.objects.get(key='cotizaciones'),
            activo=False,
        )
        cache.clear()

        response = self._pull()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data[0]['modulo_cotizaciones'])
