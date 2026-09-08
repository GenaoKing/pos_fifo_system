"""
BUG-G (docs/BUGS.md): el portal cloud ya no exige rol ADMIN/SYSADMIN para
entrar -- exige tener AL MENOS UN permiso asignado. Es lo que abre el
portal a la cajera (ver foto, subir producto) sin darle mas acceso del que
su rol ya le concede via RBAC granular.

Cubre la ruta LEGACY (`LegacyPortalTokenObtainPairSerializer`, la que corre
cuando `tenancy_enabled()` es False -- el caso de un settings de desarrollo/
POS local sin DB-per-tenant) via el endpoint real `/api/v1/auth/login/`, y
`_validar_usuario_portal` directo para no depender de armar todo el
andamiaje de tenancy solo para probar el gate.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import serializers
from rest_framework.test import APIClient

from apps.api.auth_views import _validar_usuario_portal
from apps.permisos import testing
from apps.permisos.catalogo import sembrar_catalogo
from apps.permisos.models import Permiso

User = get_user_model()


class ValidarUsuarioPortalTests(TestCase):
    def setUp(self):
        sembrar_catalogo(Permiso)
        self.negocio = testing.crear_negocio('Negocio Login')

    def test_admin_entra_por_acceso_total_aunque_no_tenga_rol_asignado(self):
        admin = User.objects.create_user(
            username='admin_login', email='admin_login@test.local',
            password='x', rol='ADMIN', activo=True,
        )
        _validar_usuario_portal(admin)  # no debe lanzar

    def test_cajera_con_rol_asignado_entra(self):
        cajera = User.objects.create_user(
            username='cajera_login', email='cajera_login@test.local',
            password='x', rol='CAJERA', activo=True,
        )
        rol = testing.crear_rol(self.negocio, 'Cajero', ['productos.ver', 'productos.fotografiar'])
        testing.asignar(cajera, rol)

        _validar_usuario_portal(cajera)  # no debe lanzar

    def test_cajera_sin_ninguna_asignacion_de_rol_es_rechazada(self):
        cajera = User.objects.create_user(
            username='cajera_sin_rol', email='cajera_sin_rol@test.local',
            password='x', rol='CAJERA', activo=True,
        )
        with self.assertRaises(serializers.ValidationError) as ctx:
            _validar_usuario_portal(cajera)
        self.assertEqual(ctx.exception.get_codes()['detail'], 'sin_permisos_portal')

    def test_usuario_inactivo_se_rechaza_antes_de_mirar_permisos(self):
        cajera = User.objects.create_user(
            username='cajera_inactiva', email='cajera_inactiva@test.local',
            password='x', rol='CAJERA', activo=False,
        )
        with self.assertRaises(serializers.ValidationError) as ctx:
            _validar_usuario_portal(cajera)
        self.assertEqual(ctx.exception.get_codes()['detail'], 'usuario_inactivo')


class LoginLegacyEndpointTests(TestCase):
    """Ruta real /api/v1/auth/login/ sin tenancy (settings de desarrollo)."""

    url = '/api/v1/auth/login/'

    def setUp(self):
        sembrar_catalogo(Permiso)
        self.negocio = testing.crear_negocio('Negocio Login Endpoint')

    def test_cajera_con_permisos_puede_loguearse_en_el_portal(self):
        cajera = User.objects.create_user(
            username='cajera_endpoint', email='cajera_endpoint@test.local',
            password='Prueba123', rol='CAJERA', activo=True, negocio=self.negocio,
        )
        rol = testing.crear_rol(self.negocio, 'Cajero', ['productos.ver', 'productos.fotografiar'])
        testing.asignar(cajera, rol)

        response = APIClient().post(
            self.url, {'username': 'cajera_endpoint', 'password': 'Prueba123'}, format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('productos.fotografiar', response.data['user']['permisos'])
        self.assertNotIn('productos.editar', response.data['user']['permisos'])

    def test_cajera_sin_permisos_recibe_400_con_el_code(self):
        User.objects.create_user(
            username='cajera_sin_permisos', email='cajera_sin_permisos@test.local',
            password='Prueba123', rol='CAJERA', activo=True, negocio=self.negocio,
        )

        response = APIClient().post(
            self.url, {'username': 'cajera_sin_permisos', 'password': 'Prueba123'}, format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'][0].code, 'sin_permisos_portal')
