"""
Tests del motor de permisos (apps/permisos/engine.py).

Cubren: default-deny, acceso total (SYSADMIN/superuser), diferenciacion del
mismo rol entre negocios, scope por sucursal, e invalidacion de cache.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.permisos import engine, testing
from apps.permisos.catalogo import sembrar_catalogo
from apps.permisos.models import Permiso
from apps.sucursales.models import Sucursal

User = get_user_model()


def _user(username, rol='CAJERA'):
    return User.objects.create_user(
        username=username, email=f'{username}@example.com', password='x', rol=rol
    )


class EngineTests(TestCase):
    def setUp(self):
        sembrar_catalogo(Permiso)
        self.negocio_a = testing.crear_negocio('Royal Plast')
        self.negocio_b = testing.crear_negocio('SK Performance')

    def test_usuario_sin_roles_no_tiene_permisos(self):
        u = _user('sin_roles')
        self.assertEqual(engine.permisos_de_usuario(u), set())
        self.assertFalse(u.tiene_permiso('clientes.crear'))

    def test_usuario_anonimo_no_tiene_permisos(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertEqual(engine.permisos_de_usuario(AnonymousUser()), set())

    def test_sysadmin_tiene_todos(self):
        u = _user('sys', rol='SYSADMIN')
        self.assertTrue(u.tiene_permiso('clientes.crear'))
        self.assertTrue(u.tiene_permiso('permisos.administrar'))

    def test_superuser_tiene_todos(self):
        u = User.objects.create_superuser('root', 'root@example.com', 'x')
        self.assertTrue(u.tiene_permiso('ventas.anular'))

    def test_acceso_total_no_depende_del_catalogo(self):
        """Robustez: un admin no queda bloqueado aunque el catalogo este vacio
        o el codigo no exista (corto-circuito en tiene_permiso)."""
        Permiso.objects.all().delete()
        admin = _user('admin_sin_catalogo', rol='ADMIN')
        self.assertTrue(admin.tiene_permiso('clientes.crear'))
        self.assertTrue(admin.tiene_permiso('codigo.inexistente'))

    def test_mismo_rol_distinto_negocio_distintos_permisos(self):
        """El nucleo del requerimiento: 'Cajero' configurado distinto por negocio."""
        rol_a = testing.crear_rol(
            self.negocio_a, 'Cajero', ['clientes.crear', 'compras.registrar']
        )
        rol_b = testing.crear_rol(self.negocio_b, 'Cajero', ['ventas.crear'])

        cajero_rp = _user('cajero_rp')
        cajero_sk = _user('cajero_sk')
        testing.asignar(cajero_rp, rol_a)
        testing.asignar(cajero_sk, rol_b)

        self.assertTrue(cajero_rp.tiene_permiso('clientes.crear'))
        self.assertTrue(cajero_rp.tiene_permiso('compras.registrar'))

        self.assertFalse(cajero_sk.tiene_permiso('clientes.crear'))
        self.assertFalse(cajero_sk.tiene_permiso('compras.registrar'))
        self.assertTrue(cajero_sk.tiene_permiso('ventas.crear'))

    def test_invalidacion_cache_al_agregar_permiso(self):
        rol = testing.crear_rol(self.negocio_a, 'Cajero', ['ventas.crear'])
        u = _user('caja')
        testing.asignar(u, rol)

        self.assertFalse(u.tiene_permiso('clientes.crear'))  # cachea el set
        rol.permisos.add(Permiso.objects.get(codigo='clientes.crear'))  # m2m_changed
        self.assertTrue(u.tiene_permiso('clientes.crear'))

    def test_rol_inactivo_no_otorga_permisos(self):
        rol = testing.crear_rol(self.negocio_a, 'Temporal', ['clientes.crear'])
        u = _user('temp')
        testing.asignar(u, rol)
        self.assertTrue(u.tiene_permiso('clientes.crear'))

        rol.activo = False
        rol.save()  # post_save -> invalida cache
        self.assertFalse(u.tiene_permiso('clientes.crear'))

    def test_scope_por_sucursal(self):
        suc1 = Sucursal.objects.create(
            codigo='RP-001', nombre='RP Tienda 1', activa=True, negocio=self.negocio_a
        )
        suc2 = Sucursal.objects.create(
            codigo='RP-002', nombre='RP Tienda 2', activa=True, negocio=self.negocio_a
        )
        rol = testing.crear_rol(self.negocio_a, 'Cajero', ['ventas.anular'])
        u = _user('caja_scope')
        testing.asignar(u, rol, sucursal=suc1)

        # Acotado a suc1: aplica en suc1, no en suc2.
        self.assertTrue(u.tiene_permiso('ventas.anular', sucursal=suc1))
        self.assertFalse(u.tiene_permiso('ventas.anular', sucursal=suc2))
