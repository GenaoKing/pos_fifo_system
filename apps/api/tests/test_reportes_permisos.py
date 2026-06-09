"""
PR1 — adopción de permisos granulares en reportes, cuentas_por_cobrar y sucursales.

Verifica el límite de permiso (default-deny) en estos endpoints, que antes usaban
EsAdminOSysadmin / EsSoloLectura:
  - reportes (ver / consolidado.ver)
  - cuentas_por_cobrar.ver (más el path de token de sucursal para sync)
  - sucursales.ver
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.permisos import testing
from apps.sucursales.models import Sucursal

User = get_user_model()


def _cajera(username):
    return User.objects.create_user(
        username=username, email=f'{username}@example.com', password='x', rol='CAJERA'
    )


class ReportesPermisoTests(TestCase):
    def setUp(self):
        self.negocio = testing.crear_negocio('Royal Plast')
        self.rol_reportes = testing.crear_rol(
            self.negocio, 'Analista', ['reportes.ver', 'reportes.consolidado.ver']
        )
        self.con_permiso = _cajera('analista')
        testing.asignar(self.con_permiso, self.rol_reportes)
        self.sin_permiso = _cajera('cajero_pelado')

    def _api(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_ventas_hoy_sin_permiso_403(self):
        r = self._api(self.sin_permiso).get('/api/v1/reportes/ventas-hoy/')
        self.assertEqual(r.status_code, 403)

    def test_ventas_hoy_con_permiso_pasa_el_gate(self):
        r = self._api(self.con_permiso).get('/api/v1/reportes/ventas-hoy/')
        self.assertNotEqual(r.status_code, 403)

    def test_comparativo_requiere_consolidado_ver(self):
        # 'Solo Ventas' no tiene reportes.consolidado.ver
        solo_ventas = _cajera('solo_ventas')
        rol = testing.crear_rol(self.negocio, 'Solo Ventas', ['reportes.ver'])
        testing.asignar(solo_ventas, rol)
        r = self._api(solo_ventas).get('/api/v1/reportes/comparativo/')
        self.assertEqual(r.status_code, 403)

    def test_admin_acceso_total(self):
        admin = User.objects.create_user(
            username='admin', email='a@e.com', password='x', rol='ADMIN'
        )
        r = self._api(admin).get('/api/v1/reportes/ventas-hoy/')
        self.assertNotEqual(r.status_code, 403)


class SucursalesStatusPermisoTests(TestCase):
    url = '/api/v1/sucursales/status/'

    def setUp(self):
        self.negocio = testing.crear_negocio('Royal Plast')
        self.con_permiso = _cajera('supervisor')
        rol = testing.crear_rol(self.negocio, 'Supervisor', ['sucursales.ver'])
        testing.asignar(self.con_permiso, rol)
        self.sin_permiso = _cajera('cajero_pelado')

    def _api(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_sin_permiso_403(self):
        self.assertEqual(self._api(self.sin_permiso).get(self.url).status_code, 403)

    def test_con_permiso_200(self):
        self.assertEqual(self._api(self.con_permiso).get(self.url).status_code, 200)


class CuentasPorCobrarPermisoTests(TestCase):
    url = '/api/v1/cuentas-por-cobrar/'

    def setUp(self):
        self.negocio = testing.crear_negocio('Royal Plast')
        # El endpoint compone modulo (suscripcion) x permiso: el negocio debe
        # tener el modulo cuentas_por_cobrar ademas de que el usuario tenga el permiso.
        from apps.suscripciones.models import Plan, SuscripcionNegocio
        SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=Plan.objects.get(slug='empresarial'), activa=True
        )
        self.con_permiso = _cajera('cobrador')
        rol = testing.crear_rol(self.negocio, 'Cobrador', ['cuentas_por_cobrar.ver'])
        testing.asignar(self.con_permiso, rol)
        self.sin_permiso = _cajera('cajero_pelado')

        # Token de servicio de sucursal (path de sync).
        self.svc = _cajera('svc_sd001')
        self.sucursal = Sucursal.objects.create(
            codigo='SD-001', nombre='Sucursal SD', activa=True,
            usuario_servicio=self.svc, negocio=self.negocio,
        )
        self.token = Token.objects.create(user=self.svc)

    def _api(self, user=None, token=None):
        client = APIClient()
        if token:
            client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        elif user:
            client.force_authenticate(user=user)
        return client

    def test_sin_permiso_403(self):
        self.assertEqual(self._api(user=self.sin_permiso).get(self.url).status_code, 403)

    def test_con_permiso_200(self):
        self.assertEqual(self._api(user=self.con_permiso).get(self.url).status_code, 200)

    def test_token_de_sucursal_puede_leer(self):
        self.assertEqual(self._api(token=self.token).get(self.url).status_code, 200)
