"""
Endpoints de administración de suscripciones/módulos (operador SaaS) y la
inclusión de `modulos[]` en el payload de sesión.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.negocios.models import Negocio
from apps.suscripciones import seed
from apps.suscripciones.models import Modulo, Plan, SuscripcionNegocio

User = get_user_model()


class SuscripcionAdminTests(TestCase):
    def setUp(self):
        seed.sembrar_modulos(Modulo)
        seed.crear_planes_default(Plan, Modulo)
        self.negocio = Negocio.objects.create(nombre='Royal Plast', slug='royal-plast')
        self.susc = SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=Plan.objects.get(slug='basico'), activa=True
        )
        # El operador del SaaS: SYSADMIN, sin negocio propio. El fixture
        # anterior usaba un `rol='ADMIN'` CON negocio —un administrador de
        # tenant— y funcionaba solo porque el acceso total legacy le concedia
        # `suscripciones.administrar`. Eso es PER-009: el dueno de un negocio
        # llegaba a los controles comerciales y, en una BD por tenant, podia
        # editar su propia suscripcion y sus entitlements.
        self.operador = User.objects.create_user(
            'op', 'op@e.com', 'x', rol='SYSADMIN'
        )
        self.admin = self.operador  # alias historico de los tests de abajo
        self.admin_tenant = User.objects.create_user(
            'duena', 'duena@e.com', 'x', rol='ADMIN', negocio=self.negocio
        )
        self.cajera = User.objects.create_user(
            'c', 'c@e.com', 'x', rol='CAJERA', negocio=self.negocio
        )

    def test_admin_del_tenant_no_administra_su_propia_suscripcion(self):
        """
        PER-009. Un ADMIN es el dueno de UN negocio; los planes, suscripciones
        y overrides son del operador de la plataforma. Antes llegaba a todos
        estos endpoints por acceso total.
        """
        api = self._api(self.admin_tenant)
        for ruta in (
            '/api/v1/suscripciones/modulos/',
            '/api/v1/suscripciones/planes/',
            '/api/v1/suscripciones/negocios/',
            '/api/v1/suscripciones/overrides/',
        ):
            with self.subTest(ruta=ruta):
                self.assertEqual(api.get(ruta).status_code, 403)

    def test_el_admin_del_tenant_tampoco_puede_cambiarse_el_plan(self):
        r = self._api(self.admin_tenant).patch(
            f'/api/v1/suscripciones/negocios/{self.susc.id}/',
            {'plan': Plan.objects.get(slug='empresarial').id}, format='json',
        )
        self.assertEqual(r.status_code, 403)
        self.susc.refresh_from_db()
        self.assertEqual(self.susc.plan.slug, 'basico')

    def _api(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_cajera_sin_permiso_403(self):
        self.assertEqual(
            self._api(self.cajera).get('/api/v1/suscripciones/planes/').status_code, 403
        )

    def test_lista_modulos(self):
        r = self._api(self.admin).get('/api/v1/suscripciones/modulos/')
        self.assertEqual(r.status_code, 200)
        keys = {m['key'] for m in r.data}
        self.assertIn('cuentas_por_cobrar', keys)
        self.assertIn('ventas', keys)

    def test_lista_planes(self):
        r = self._api(self.admin).get('/api/v1/suscripciones/planes/')
        self.assertEqual(r.status_code, 200)
        slugs = {p['slug'] for p in r.data}
        self.assertEqual(slugs, {'basico', 'pro', 'empresarial'})

    def test_cambiar_plan_actualiza_modulos_activos(self):
        url = f'/api/v1/suscripciones/negocios/{self.susc.id}/'
        r = self._api(self.admin).patch(url, {'plan': 'empresarial'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn('ecf', r.data['modulos_activos'])
        self.assertIn('cuentas_por_cobrar', r.data['modulos_activos'])

    def test_override_incluir_a_la_carta(self):
        r = self._api(self.admin).post(
            '/api/v1/suscripciones/overrides/',
            {'negocio': self.negocio.id, 'modulo': 'ecf', 'incluido': True},
            format='json',
        )
        self.assertEqual(r.status_code, 201, r.data)

    def test_override_excluir_bloqueado_por_dependientes(self):
        self.susc.plan = Plan.objects.get(slug='empresarial')
        self.susc.save()
        # cuentas_por_cobrar (activo) depende de ventas -> no se puede excluir ventas.
        r = self._api(self.admin).post(
            '/api/v1/suscripciones/overrides/',
            {'negocio': self.negocio.id, 'modulo': 'ventas', 'incluido': False},
            format='json',
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn('modulo', r.data)


class PayloadModulosTests(TestCase):
    def setUp(self):
        seed.sembrar_modulos(Modulo)
        seed.crear_planes_default(Plan, Modulo)
        self.negocio = Negocio.objects.create(nombre='RP', slug='rp')
        SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=Plan.objects.get(slug='empresarial'), activa=True
        )
        self.user = User.objects.create_user(
            'u', 'u@e.com', 'x', rol='ADMIN', negocio=self.negocio
        )

    def test_me_incluye_modulos(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        r = client.get('/api/v1/auth/me/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('modulos', r.data)
        self.assertIn('ecf', r.data['modulos'])
        self.assertIn('cuentas_por_cobrar', r.data['modulos'])
