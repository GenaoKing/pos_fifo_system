"""
Tests del seed de roles (apps/permisos/seed.py).

Cubre: el rol Cajero por defecto NO incluye 'ventas.anular' (anular es
ADMIN/SYSADMIN), y que re-ejecutar catalogo/bootstrap NO pisa personalizaciones
ni reactiva revocaciones.
"""
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.permisos import testing
from apps.permisos.catalogo import sembrar_catalogo
from apps.permisos.models import AsignacionRol, Permiso, Rol
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

    def test_sync_catalogo_no_pisa_roles_sin_flag_explicito(self):
        admin, _ = crear_roles_default(self.negocio, Rol, Permiso)
        retirado = Permiso.objects.get(codigo='ventas.anular')
        admin.permisos.remove(retirado)

        call_command('sync_permisos', stdout=StringIO())
        self.assertFalse(admin.permisos.filter(pk=retirado.pk).exists())

        call_command(
            'sync_permisos',
            aplicar_presets_sistema=True,
            stdout=StringIO(),
        )
        self.assertTrue(admin.permisos.filter(pk=retirado.pk).exists())

    def test_bootstrap_no_elige_el_primer_negocio_si_hay_varios(self):
        testing.crear_negocio('Segundo negocio')

        with self.assertRaisesMessage(CommandError, 'Hay varios negocios'):
            call_command('bootstrap_negocio', stdout=StringIO())

    def test_bootstrap_repetido_no_reactiva_asignacion_revocada(self):
        Usuario = get_user_model()
        usuario = Usuario.objects.create_user(
            username='cajera_seed',
            email='cajera_seed@test.local',
            password='pass',
            rol='CAJERA',
            activo=True,
            negocio=self.negocio,
        )
        _, cajero = crear_roles_default(self.negocio, Rol, Permiso)
        asignacion = AsignacionRol.objects.create(
            usuario=usuario,
            rol=cajero,
            activo=False,
        )

        call_command(
            'bootstrap_negocio',
            negocio_id=self.negocio.id,
            stdout=StringIO(),
        )

        asignacion.refresh_from_db()
        self.assertFalse(asignacion.activo)
        self.assertEqual(
            AsignacionRol.objects.filter(usuario=usuario, rol=cajero).count(),
            1,
        )

    def test_preflight_bypass_admin_exige_asignacion_explicita(self):
        Usuario = get_user_model()
        admin = Usuario.objects.create_user(
            username='admin_cutover',
            email='admin_cutover@test.local',
            password='pass',
            rol='ADMIN',
            activo=True,
            negocio=self.negocio,
        )

        with self.assertRaisesMessage(CommandError, 'No desactivar'):
            call_command('preflight_rbac_admin_cutover', stdout=StringIO())

        rol = testing.crear_rol(
            self.negocio,
            'Administrador cutover',
            ['permisos.administrar'],
        )
        testing.asignar(admin, rol, set_negocio=False)
        salida = StringIO()
        call_command('preflight_rbac_admin_cutover', stdout=salida)

        self.assertIn('bloqueadores=0', salida.getvalue())
