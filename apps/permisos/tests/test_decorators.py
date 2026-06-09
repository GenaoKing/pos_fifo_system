"""Tests de @requiere_permiso_local (apps/permisos/decorators.py)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from apps.permisos import testing
from apps.permisos.catalogo import sembrar_catalogo
from apps.permisos.decorators import requiere_permiso_local
from apps.permisos.models import Permiso

User = get_user_model()


@requiere_permiso_local('compras.registrar')
def _vista(request):
    return HttpResponse('OK')


class RequierePermisoLocalTests(TestCase):
    def setUp(self):
        sembrar_catalogo(Permiso)
        self.factory = RequestFactory()
        self.negocio = testing.crear_negocio('Royal Plast')

    def _req(self, user):
        req = self.factory.get('/x/')
        req.user = user
        return req

    def test_no_autenticado_redirige_login(self):
        resp = _vista(self._req(AnonymousUser()))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.url)

    def test_sin_permiso_redirige(self):
        u = User.objects.create_user('c', 'c@e.com', 'x', rol='CAJERA')
        resp = _vista(self._req(u))
        self.assertEqual(resp.status_code, 302)

    def test_con_permiso_pasa(self):
        rol = testing.crear_rol(self.negocio, 'Compras', ['compras.registrar'])
        u = User.objects.create_user('c', 'c@e.com', 'x', rol='CAJERA')
        testing.asignar(u, rol)
        resp = _vista(self._req(u))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b'OK')

    def test_admin_acceso_total(self):
        u = User.objects.create_user('a', 'a@e.com', 'x', rol='ADMIN')
        resp = _vista(self._req(u))
        self.assertEqual(resp.status_code, 200)
