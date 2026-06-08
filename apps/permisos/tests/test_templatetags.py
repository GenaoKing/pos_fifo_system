"""Tests del filtro `puede` (apps/permisos/templatetags/permisos.py)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template
from django.test import TestCase

from apps.permisos import testing
from apps.permisos.catalogo import sembrar_catalogo
from apps.permisos.models import Permiso

User = get_user_model()

TPL = "{% load permisos %}{% if user|puede:codigo %}SI{% else %}NO{% endif %}"


def _render(user, codigo):
    return Template(TPL).render(Context({'user': user, 'codigo': codigo}))


class PuedeFilterTests(TestCase):
    def setUp(self):
        sembrar_catalogo(Permiso)
        self.negocio = testing.crear_negocio('Royal Plast')

    def test_anonimo_no_puede(self):
        self.assertEqual(_render(AnonymousUser(), 'clientes.crear'), 'NO')

    def test_cajero_segun_su_rol(self):
        rol = testing.crear_rol(self.negocio, 'Cajero', ['clientes.crear'])
        u = User.objects.create_user('c', 'c@e.com', 'x', rol='CAJERA')
        testing.asignar(u, rol)
        self.assertEqual(_render(u, 'clientes.crear'), 'SI')
        self.assertEqual(_render(u, 'compras.registrar'), 'NO')

    def test_admin_acceso_total(self):
        u = User.objects.create_user('a', 'a@e.com', 'x', rol='ADMIN')
        self.assertEqual(_render(u, 'compras.registrar'), 'SI')
