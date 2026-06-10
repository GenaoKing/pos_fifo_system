"""
Endpoint cloud GET /api/v1/sync/roles/ — definiciones de rol del negocio de la
sucursal autenticada (para el sync cloud→local).
"""
import datetime
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.permisos import testing
from apps.permisos.catalogo import sembrar_catalogo
from apps.permisos.models import AsignacionRol, Permiso
from apps.sucursales.models import Sucursal

User = get_user_model()


class SyncRolesEndpointTests(TestCase):
    url = '/api/v1/sync/roles/'

    def setUp(self):
        sembrar_catalogo(Permiso)
        self.neg_a = testing.crear_negocio('Royal Plast')
        self.neg_b = testing.crear_negocio('SK Performance')
        self.rol_a = testing.crear_rol(
            self.neg_a, 'Cajero', ['clientes.ver', 'compras.registrar']
        )
        testing.crear_rol(self.neg_b, 'Cajero', ['clientes.ver'])

        self.svc = User.objects.create_user('svc', 's@e.com', 'x', rol='CAJERA')
        self.sucursal = Sucursal.objects.create(
            codigo='RP-001', nombre='RP', activa=True,
            negocio=self.neg_a, usuario_servicio=self.svc,
        )
        self.token = Token.objects.create(user=self.svc)

    def _api(self, token=None):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {(token or self.token).key}')
        return client

    def test_devuelve_roles_de_su_negocio_con_permisos(self):
        r = self._api().get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data), 1)  # solo el rol del negocio A
        cajero = r.data[0]
        self.assertEqual(cajero['slug'], 'cajero')
        self.assertIn('compras.registrar', cajero['permisos'])

    def test_sucursal_sin_negocio_devuelve_vacio(self):
        svc2 = User.objects.create_user('svc2', 's2@e.com', 'x', rol='CAJERA')
        Sucursal.objects.create(
            codigo='X-001', nombre='X', activa=True, usuario_servicio=svc2
        )
        token2 = Token.objects.create(user=svc2)
        r = self._api(token=token2).get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, [])

    def test_desde_filtra_incremental(self):
        futuro = (timezone.now() + datetime.timedelta(hours=1)).isoformat()
        r = self._api().get(f'{self.url}?desde={quote(futuro)}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, [])


class SyncAsignacionesEndpointTests(TestCase):
    url = '/api/v1/sync/asignaciones/'

    def setUp(self):
        sembrar_catalogo(Permiso)
        self.neg_a = testing.crear_negocio('Royal Plast')
        self.neg_b = testing.crear_negocio('SK Performance')
        self.rol_a = testing.crear_rol(self.neg_a, 'Cajero', ['clientes.ver'])
        self.rol_b = testing.crear_rol(self.neg_b, 'Cajero', ['clientes.ver'])

        self.user_global = User.objects.create_user(
            'caja_global', 'cg@e.com', 'x', rol='CAJERA', negocio=self.neg_a
        )
        self.user_sucursal = User.objects.create_user(
            'caja_rp1', 'c1@e.com', 'x', rol='CAJERA', negocio=self.neg_a
        )
        self.user_otra_sucursal = User.objects.create_user(
            'caja_rp2', 'c2@e.com', 'x', rol='CAJERA', negocio=self.neg_a
        )
        self.user_otro_negocio = User.objects.create_user(
            'caja_sk', 'sk@e.com', 'x', rol='CAJERA', negocio=self.neg_b
        )

        self.svc = User.objects.create_user('svc_asig', 'sa@e.com', 'x', rol='CAJERA')
        self.sucursal = Sucursal.objects.create(
            codigo='RP-001', nombre='RP 1', activa=True,
            negocio=self.neg_a, usuario_servicio=self.svc,
        )
        self.otra_sucursal = Sucursal.objects.create(
            codigo='RP-002', nombre='RP 2', activa=True, negocio=self.neg_a
        )
        self.sucursal_b = Sucursal.objects.create(
            codigo='SK-001', nombre='SK', activa=True, negocio=self.neg_b
        )
        self.token = Token.objects.create(user=self.svc)

        AsignacionRol.objects.create(usuario=self.user_global, rol=self.rol_a)
        AsignacionRol.objects.create(
            usuario=self.user_sucursal, rol=self.rol_a, sucursal=self.sucursal
        )
        AsignacionRol.objects.create(
            usuario=self.user_otra_sucursal, rol=self.rol_a, sucursal=self.otra_sucursal
        )
        AsignacionRol.objects.create(
            usuario=self.user_otro_negocio, rol=self.rol_b, sucursal=self.sucursal_b
        )

    def _api(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        return client

    def test_devuelve_globales_y_de_esta_sucursal(self):
        r = self._api().get(self.url)
        self.assertEqual(r.status_code, 200)
        usernames = {row['usuario_username'] for row in r.data}
        self.assertEqual(usernames, {'caja_global', 'caja_rp1'})

        fila_sucursal = next(row for row in r.data if row['usuario_username'] == 'caja_rp1')
        self.assertEqual(fila_sucursal['rol_slug'], 'cajero')
        self.assertEqual(fila_sucursal['sucursal_codigo'], 'RP-001')
        self.assertTrue(fila_sucursal['activo'])

        fila_global = next(row for row in r.data if row['usuario_username'] == 'caja_global')
        self.assertIsNone(fila_global['sucursal_codigo'])

    def test_desde_filtra_incremental(self):
        futuro = (timezone.now() + datetime.timedelta(hours=1)).isoformat()
        r = self._api().get(f'{self.url}?desde={quote(futuro)}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, [])
