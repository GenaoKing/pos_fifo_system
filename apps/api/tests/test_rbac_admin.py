"""
PR2 — endpoints de administración de RBAC (catálogo, roles, asignaciones).

Verifica: gating por `permisos.administrar`, scoping por negocio (aislamiento
cross-tenant), que editar los permisos de un rol cambia el comportamiento
efectivo, y el rechazo de asignaciones cross-negocio.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.permisos import testing
from apps.permisos.catalogo import sembrar_catalogo
from apps.permisos.models import Permiso
from apps.sucursales.models import Sucursal

User = get_user_model()

CATALOGO_URL = '/api/v1/permisos/catalogo/'
ROLES_URL = '/api/v1/permisos/roles/'
ASIGN_URL = '/api/v1/permisos/asignaciones/'
USUARIOS_URL = '/api/v1/permisos/usuarios/'
SUCURSALES_URL = '/api/v1/permisos/sucursales/'


class RbacAdminTests(TestCase):
    def setUp(self):
        sembrar_catalogo(Permiso)
        self.neg_a = testing.crear_negocio('Royal Plast')
        self.neg_b = testing.crear_negocio('SK Performance')

        self.admin_a = User.objects.create_user(
            username='admin_a', email='aa@e.com', password='x',
            rol='ADMIN', negocio=self.neg_a,
        )
        self.admin_b = User.objects.create_user(
            username='admin_b', email='ab@e.com', password='x',
            rol='ADMIN', negocio=self.neg_b,
        )
        self.cajera = User.objects.create_user(
            username='cajera', email='c@e.com', password='x',
            rol='CAJERA', negocio=self.neg_a,
        )

        self.rol_a = testing.crear_rol(self.neg_a, 'Cajero', ['clientes.ver'])
        self.rol_b = testing.crear_rol(self.neg_b, 'Cajero', ['clientes.ver'])

    def _api(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    # --- Gating ---

    def test_cajera_sin_permiso_administrar_403(self):
        self.assertEqual(self._api(self.cajera).get(ROLES_URL).status_code, 403)

    def test_catalogo_lista_permisos(self):
        r = self._api(self.admin_a).get(CATALOGO_URL)
        self.assertEqual(r.status_code, 200)
        codigos = {p['codigo'] for p in r.data}
        self.assertIn('clientes.crear', codigos)
        self.assertIn('permisos.administrar', codigos)

    # --- Scoping por negocio ---

    def test_admin_ve_solo_roles_de_su_negocio(self):
        r = self._api(self.admin_a).get(ROLES_URL)
        self.assertEqual(r.status_code, 200)
        ids = {row['id'] for row in r.data}
        self.assertIn(self.rol_a.id, ids)
        self.assertNotIn(self.rol_b.id, ids)

    def test_admin_no_accede_a_rol_de_otro_negocio(self):
        r = self._api(self.admin_a).get(f'{ROLES_URL}{self.rol_b.id}/')
        self.assertEqual(r.status_code, 404)

    def test_admin_no_edita_rol_de_otro_negocio(self):
        r = self._api(self.admin_a).patch(
            f'{ROLES_URL}{self.rol_b.id}/', {'descripcion': 'hackeado'}, format='json'
        )
        self.assertEqual(r.status_code, 404)

    # --- CRUD ---

    def test_crear_rol_fuerza_negocio_y_slug(self):
        r = self._api(self.admin_a).post(
            ROLES_URL,
            {'nombre': 'Supervisor', 'permisos': ['clientes.ver', 'clientes.crear']},
            format='json',
        )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['negocio'], self.neg_a.id)
        self.assertEqual(r.data['slug'], 'supervisor')
        self.assertCountEqual(r.data['permisos'], ['clientes.ver', 'clientes.crear'])

    def test_no_se_puede_eliminar_rol_de_sistema(self):
        self.rol_a.es_sistema = True
        self.rol_a.save()
        r = self._api(self.admin_a).delete(f'{ROLES_URL}{self.rol_a.id}/')
        self.assertEqual(r.status_code, 403)

    # --- El editar permisos cambia el comportamiento efectivo ---

    def test_editar_permisos_de_rol_cambia_enforcement(self):
        usuario = User.objects.create_user(
            username='caja_rp', email='cr@e.com', password='x',
            rol='CAJERA', negocio=self.neg_a,
        )
        testing.asignar(usuario, self.rol_a, set_negocio=False)
        self.assertFalse(usuario.tiene_permiso('clientes.crear'))

        r = self._api(self.admin_a).patch(
            f'{ROLES_URL}{self.rol_a.id}/',
            {'permisos': ['clientes.ver', 'clientes.crear']},
            format='json',
        )
        self.assertEqual(r.status_code, 200, r.data)
        # La signal m2m invalidó el cache → el cajero ahora sí puede crear.
        self.assertTrue(usuario.tiene_permiso('clientes.crear'))

    # --- Asignaciones ---

    def test_asignar_rol_de_su_negocio_ok(self):
        usuario = User.objects.create_user(
            username='nuevo', email='n@e.com', password='x',
            rol='CAJERA', negocio=self.neg_a,
        )
        r = self._api(self.admin_a).post(
            ASIGN_URL, {'usuario': usuario.id, 'rol': self.rol_a.id}, format='json'
        )
        self.assertEqual(r.status_code, 201, r.data)

    def test_borrar_asignacion_es_soft_delete(self):
        usuario = User.objects.create_user(
            username='soft', email='s@e.com', password='x',
            rol='CAJERA', negocio=self.neg_a,
        )
        asign = testing.asignar(usuario, self.rol_a, set_negocio=False)
        r = self._api(self.admin_a).delete(f'{ASIGN_URL}{asign.id}/')
        self.assertEqual(r.status_code, 204)
        asign.refresh_from_db()
        self.assertFalse(asign.activo)  # la fila sigue existiendo, inactiva

    def test_reasignar_reactiva_en_vez_de_duplicar(self):
        usuario = User.objects.create_user(
            username='reasig', email='r@e.com', password='x',
            rol='CAJERA', negocio=self.neg_a,
        )
        asign = testing.asignar(usuario, self.rol_a, set_negocio=False)
        self._api(self.admin_a).delete(f'{ASIGN_URL}{asign.id}/')

        # Re-alta de la misma terna: reactiva la fila existente (200), no 400.
        r = self._api(self.admin_a).post(
            ASIGN_URL, {'usuario': usuario.id, 'rol': self.rol_a.id}, format='json'
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['id'], asign.id)
        self.assertTrue(r.data['activo'])
        self.assertEqual(usuario.asignaciones_rol.filter(rol=self.rol_a).count(), 1)

    def test_asignar_rol_cross_negocio_rechazado(self):
        usuario = User.objects.create_user(
            username='nuevo2', email='n2@e.com', password='x',
            rol='CAJERA', negocio=self.neg_a,
        )
        # admin_a intenta asignar un rol del negocio B.
        r = self._api(self.admin_a).post(
            ASIGN_URL, {'usuario': usuario.id, 'rol': self.rol_b.id}, format='json'
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn('rol', r.data)

    def test_asignaciones_scoped_por_negocio(self):
        usuario = User.objects.create_user(
            username='caja_a', email='ca@e.com', password='x',
            rol='CAJERA', negocio=self.neg_a,
        )
        testing.asignar(usuario, self.rol_a, set_negocio=False)
        # admin_b no debe ver asignaciones del negocio A.
        r = self._api(self.admin_b).get(ASIGN_URL)
        self.assertEqual(r.status_code, 200)
        usuarios = {row['usuario'] for row in r.data}
        self.assertNotIn(usuario.id, usuarios)

    # --- Usuarios asignables (selector de la UI de asignación) ---

    def test_usuarios_gated_por_administrar(self):
        self.assertEqual(self._api(self.cajera).get(USUARIOS_URL).status_code, 403)

    def test_usuarios_scoped_por_negocio(self):
        r = self._api(self.admin_a).get(USUARIOS_URL)
        self.assertEqual(r.status_code, 200)
        usernames = {u['username'] for u in r.data}
        # admin_a y cajera son del negocio A; admin_b es del negocio B.
        self.assertIn('admin_a', usernames)
        self.assertIn('cajera', usernames)
        self.assertNotIn('admin_b', usernames)

    def test_usuarios_incluye_nombre_y_rol_legacy(self):
        self.admin_a.first_name = 'Ana'
        self.admin_a.last_name = 'Ruiz'
        self.admin_a.save()
        r = self._api(self.admin_a).get(USUARIOS_URL)
        fila = next(u for u in r.data if u['username'] == 'admin_a')
        self.assertEqual(fila['nombre_completo'], 'Ana Ruiz')
        self.assertEqual(fila['rol'], 'ADMIN')

    # --- Sucursales asignables (scope opcional de la asignación) ---

    def test_sucursales_scoped_por_negocio(self):
        Sucursal.objects.create(negocio=self.neg_a, codigo='RP-001', nombre='RP Sede')
        Sucursal.objects.create(negocio=self.neg_b, codigo='SK-001', nombre='SK Sede')
        r = self._api(self.admin_a).get(SUCURSALES_URL)
        self.assertEqual(r.status_code, 200)
        codigos = {s['codigo'] for s in r.data}
        self.assertEqual(codigos, {'RP-001'})

    # --- SYSADMIN global con ?negocio= ---

    def test_sysadmin_administra_via_negocio_param(self):
        sysadmin = User.objects.create_user(
            username='root', email='root@e.com', password='x', rol='SYSADMIN',
        )
        r = self._api(sysadmin).get(f'{ROLES_URL}?negocio={self.neg_a.id}')
        self.assertEqual(r.status_code, 200)
        ids = {row['id'] for row in r.data}
        self.assertIn(self.rol_a.id, ids)
        self.assertNotIn(self.rol_b.id, ids)
