"""
Tests del seed de roles (apps/permisos/seed.py).

Cubre: el rol Cajero por defecto NO incluye 'ventas.anular' (anular es
ADMIN/SYSADMIN), y que re-ejecutar el bootstrap NO pisa personalizaciones.
"""
from django.test import TestCase

from apps.permisos import testing
from apps.permisos.catalogo import sembrar_catalogo
from apps.permisos.models import Permiso, Rol
from apps.permisos.seed import crear_roles_default


class CrearRolesDefaultTests(TestCase):
    def setUp(self):
        sembrar_catalogo(Permiso)
        self.negocio = testing.crear_negocio('Royal Plast')

    def test_admin_default_tiene_todos_los_permisos(self):
        admin, _ = crear_roles_default(self.negocio, Rol, Permiso)
        self.assertEqual(admin.permisos.count(), Permiso.objects.count())

    def test_cajero_default_no_incluye_anular(self):
        _, cajero = crear_roles_default(self.negocio, Rol, Permiso)
        codigos = set(cajero.permisos.values_list('codigo', flat=True))
        self.assertIn('ventas.crear', codigos)
        self.assertIn('ventas.aplicar_descuento', codigos)
        self.assertIn('ventas.reimprimir', codigos)
        self.assertNotIn('ventas.anular', codigos)

    def test_rerun_no_pisa_personalizacion(self):
        _, cajero = crear_roles_default(self.negocio, Rol, Permiso)
        cajero.permisos.add(Permiso.objects.get(codigo='clientes.crear'))

        # Re-ejecutar bootstrap NO debe quitar la personalizacion del admin.
        crear_roles_default(self.negocio, Rol, Permiso)

        codigos = set(cajero.permisos.values_list('codigo', flat=True))
        self.assertIn('clientes.crear', codigos)
