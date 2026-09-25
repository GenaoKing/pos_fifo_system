"""Contrato HTTP C04 p5.2: escrituras administrativas tenant-scoped."""
from unittest.mock import patch
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient

from apps.auditoria.models import Auditoria
from apps.configuracion.models import ConfiguracionNegocio
from apps.permisos import testing
from apps.permisos.catalogo import sembrar_catalogo
from apps.permisos.models import AsignacionRol, Permiso
from apps.productos.models import Categoria
from apps.sucursales.models import Sucursal
from apps.api.auth_views import _tenant_token_payload
from apps.tenancy.authentication import _autorizar_tenant
from apps.tenancy.context import force_tenancy, reset_current_tenant, set_current_tenant
from apps.tenancy.models import Identity, Membership, Tenant
from apps.tenancy.tests.test_multidb_isolation import ALIAS_A, NAMESPACE as TENANT_NAMESPACE


Usuario = get_user_model()
USUARIOS_URL = '/api/v1/administracion/usuarios/'
SUCURSALES_URL = '/api/v1/administracion/sucursales/'
CONFIGURACIONES_URL = '/api/v1/administracion/configuraciones/'


class AdministracionPortalTests(TestCase):
    def setUp(self):
        sembrar_catalogo(Permiso)
        self.negocio_a = testing.crear_negocio('Royal Plast')
        self.negocio_b = testing.crear_negocio('SK Performance')
        self.admin_a = Usuario.objects.create_user(
            username='admin_a', email='admin_a@example.test', password='A9!clave-segura',
            rol='ADMIN', negocio=self.negocio_a,
        )
        self.cajera_a = Usuario.objects.create_user(
            username='cajera_a', email='cajera_a@example.test', password='A9!clave-segura',
            rol='CAJERA', negocio=self.negocio_a,
        )
        self.usuario_b = Usuario.objects.create_user(
            username='usuario_b', email='usuario_b@example.test', password='A9!clave-segura',
            rol='CAJERA', negocio=self.negocio_b,
        )
        self.rol_cajero = testing.crear_rol(
            self.negocio_a, 'Cajero C04', ['ventas.crear'],
        )
        self.sucursal_a = Sucursal.objects.create(
            negocio=self.negocio_a, codigo='RP-C04', nombre='Royal C04',
        )
        self.sucursal_b = Sucursal.objects.create(
            negocio=self.negocio_b, codigo='SK-C04', nombre='SK C04',
        )
        self.config_a = ConfiguracionNegocio.objects.create(
            sucursal=self.sucursal_a, nombre_negocio='Royal Plast',
        )
        self.config_b = ConfiguracionNegocio.objects.create(
            sucursal=self.sucursal_b, nombre_negocio='SK Performance',
        )

    def _api(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_usuarios_alta_es_scoped_auditable_y_no_filtra_password(self):
        response = self._api(self.admin_a).post(
            USUARIOS_URL,
            {
                'username': 'nueva.cajera',
                'email': 'nueva.cajera@example.test',
                'password': 'A9!clave-segura-nueva',
                'first_name': 'Nueva',
                'last_name': 'Cajera',
                'rol_asignacion': self.rol_cajero.pk,
                'sucursal': self.sucursal_a.pk,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn('password', response.data)
        usuario = Usuario.objects.get(pk=response.data['id'])
        self.assertEqual(usuario.negocio_id, self.negocio_a.pk)
        self.assertEqual(usuario.get_full_name(), 'Nueva Cajera')
        asignacion = AsignacionRol.objects.get(usuario=usuario, rol=self.rol_cajero)
        self.assertEqual(asignacion.sucursal_id, self.sucursal_a.pk)
        evento = Auditoria.objects.get(
            accion='usuarios.usuario.provisionado', object_id=usuario.pk,
        )
        self.assertNotIn('password', str(evento.datos_nuevos))

    def test_usuarios_fuera_del_negocio_dan_404_y_cajera_no_administra(self):
        client = self._api(self.admin_a)
        self.assertEqual(client.get(f'{USUARIOS_URL}{self.usuario_b.pk}/').status_code, 404)
        self.assertEqual(self._api(self.cajera_a).get(USUARIOS_URL).status_code, 403)

    def test_email_y_username_son_inmutables_y_baja_es_logica_idempotente(self):
        objetivo = Usuario.objects.create_user(
            username='baja_local', email='baja_local@example.test', password='A9!clave-segura',
            rol='CAJERA', negocio=self.negocio_a,
        )
        client = self._api(self.admin_a)
        rechazo = client.patch(
            f'{USUARIOS_URL}{objetivo.pk}/',
            {'email': 'otro@example.test', 'motivo': 'No debe cambiar login'},
            format='json',
        )
        self.assertEqual(rechazo.status_code, 400, rechazo.data)

        primera = client.delete(
            f'{USUARIOS_URL}{objetivo.pk}/', {'motivo': 'Fin de relacion'}, format='json'
        )
        segunda = client.delete(
            f'{USUARIOS_URL}{objetivo.pk}/', {'motivo': 'Reintento seguro'}, format='json'
        )
        self.assertEqual(primera.status_code, 204)
        self.assertEqual(segunda.status_code, 204)
        objetivo.refresh_from_db()
        self.assertFalse(objetivo.activo)
        self.assertEqual(
            Auditoria.objects.filter(
                accion='usuarios.usuario.desactivado', object_id=objetivo.pk,
            ).count(),
            1,
        )

    @patch('apps.api.views.administracion.tenancy_enabled', return_value=True)
    def test_alta_y_baja_cloud_crean_y_revocan_membership(self, _tenancy_enabled):
        tenant = Tenant.objects.create(
            tenant_key='rp-c04', slug='rp-c04', nombre='Royal Plast C04',
        )
        self.admin_a.tenant_key = tenant.tenant_key
        client = self._api(self.admin_a)
        response = client.post(
            USUARIOS_URL,
            {
                'username': 'portal.cajera',
                'email': 'portal.cajera@example.test',
                'password': 'A9!clave-segura-nueva',
                'first_name': 'Portal',
                'last_name': 'Cajera',
                'rol_asignacion': self.rol_cajero.pk,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        usuario = Usuario.objects.get(pk=response.data['id'])
        identity = Identity.objects.get(email='portal.cajera@example.test')
        membership = Membership.objects.get(identity=identity, tenant=tenant)
        self.assertTrue(membership.activo)
        self.assertEqual(membership.username, usuario.username)

        baja = client.delete(
            f'{USUARIOS_URL}{usuario.pk}/', {'motivo': 'Revocar acceso'}, format='json'
        )
        self.assertEqual(baja.status_code, 204, baja.data)
        usuario.refresh_from_db()
        membership.refresh_from_db()
        self.assertFalse(usuario.activo)
        self.assertFalse(membership.activo)
        with self.assertRaises(AuthenticationFailed):
            _autorizar_tenant(
                identity=identity, tenant=tenant, username=usuario.username, impersonado=False,
            )

    def test_sucursales_crud_logico_no_expone_claves_y_conserva_aislamiento(self):
        client = self._api(self.admin_a)
        creada = client.post(
            SUCURSALES_URL,
            {'codigo': 'rp-nueva', 'nombre': 'Royal Nueva', 'telefono': '809-555-0101'},
            format='json',
        )
        self.assertEqual(creada.status_code, 201, creada.data)
        self.assertEqual(creada.data['codigo'], 'RP-NUEVA')
        self.assertNotIn('api_key', creada.data)
        sucursal = Sucursal.objects.get(pk=creada.data['id'])
        self.assertEqual(sucursal.negocio_id, self.negocio_a.pk)
        self.assertEqual(
            Auditoria.objects.filter(
                accion='sucursales.sucursal.creada', object_id=sucursal.pk,
            ).count(),
            1,
        )
        self.assertEqual(client.get(f'{SUCURSALES_URL}{self.sucursal_b.pk}/').status_code, 404)
        self.assertEqual(
            client.patch(
                f'{SUCURSALES_URL}{sucursal.pk}/',
                {'codigo': 'NO-PUEDE', 'motivo': 'No debe romper sync'},
                format='json',
            ).status_code,
            400,
        )
        baja = client.delete(
            f'{SUCURSALES_URL}{sucursal.pk}/', {'motivo': 'Cierre de sede'}, format='json'
        )
        self.assertEqual(baja.status_code, 204)
        sucursal.refresh_from_db()
        self.assertFalse(sucursal.activa)
        self.assertTrue(Sucursal.objects.filter(pk=sucursal.pk).exists())

    def test_configuracion_es_allowlist_scoped_y_auditable(self):
        client = self._api(self.admin_a)
        listado = client.get(CONFIGURACIONES_URL)
        self.assertEqual(listado.status_code, 200, listado.data)
        self.assertEqual([fila['id'] for fila in listado.data], [self.config_a.pk])
        self.assertEqual(client.get(f'{CONFIGURACIONES_URL}{self.config_b.pk}/').status_code, 404)

        actualizada = client.patch(
            f'{CONFIGURACIONES_URL}{self.config_a.pk}/',
            {
                'nombre_negocio': 'Royal Plast Actualizado',
                'cantidad_copias_ticket': 2,
                'motivo': 'Estandarizar ticket',
            },
            format='json',
        )
        self.assertEqual(actualizada.status_code, 200, actualizada.data)
        self.config_a.refresh_from_db()
        self.assertEqual(self.config_a.nombre_negocio, 'Royal Plast Actualizado')
        self.assertEqual(self.config_a.cantidad_copias_ticket, 2)
        self.assertEqual(
            Auditoria.objects.filter(
                accion='configuracion.configuracion.actualizada', object_id=self.config_a.pk,
            ).count(),
            1,
        )

        prohibida = client.patch(
            f'{CONFIGURACIONES_URL}{self.config_a.pk}/',
            {'modulo_dashboard': False, 'motivo': 'No por esta API'},
            format='json',
        )
        self.assertEqual(prohibida.status_code, 400, prohibida.data)
        self.config_a.refresh_from_db()
        self.assertTrue(self.config_a.modulo_dashboard)
        invalida = client.patch(
            f'{CONFIGURACIONES_URL}{self.config_a.pk}/',
            {
                'pago_efectivo': False,
                'pago_transferencia': False,
                'pago_tarjeta': False,
                'motivo': 'No debe dejar al POS sin cobro',
            },
            format='json',
        )
        self.assertEqual(invalida.status_code, 400, invalida.data)
        self.config_a.refresh_from_db()
        self.assertTrue(self.config_a.pago_efectivo)
        self.assertEqual(self._api(self.cajera_a).get(CONFIGURACIONES_URL).status_code, 403)


@skipUnless(TENANT_NAMESPACE, 'Requiere TENANT_TEST_DB_NAMESPACE aislado.')
@override_settings(TENANCY_DB_PER_TENANT_ENABLED=True)
class AdministracionPortalTenantFisicoTests(TestCase):
    """Prueba opcional real: control plane default y dominio en otra BD."""

    databases = {'default', ALIAS_A}

    def setUp(self):
        self._force = force_tenancy(True)
        self._force.__enter__()
        self._tokens = set_current_tenant('c04-admin-fisico', ALIAS_A)
        self.tenant = Tenant.objects.using('default').create(
            tenant_key='c04-admin-fisico', slug='c04-admin-fisico',
            nombre='C04 Admin Fisico',
        )
        sembrar_catalogo(Permiso)
        self.negocio = testing.crear_negocio('C04 Admin Fisico')
        self.admin = Usuario.objects.db_manager(ALIAS_A).create_human_user(
            username='admin_fisico', email='admin_fisico@example.test',
            password='A9!clave-segura', rol='ADMIN', negocio=self.negocio,
        )
        self.rol = testing.crear_rol(self.negocio, 'Caja fisica', ['ventas.crear'])
        self.identity_admin = Identity(
            email=self.admin.email, nombre='Admin fisico', activo=True,
        )
        self.identity_admin.set_password('A9!clave-segura')
        self.identity_admin.save(using='default')
        Membership.objects.using('default').create(
            identity=self.identity_admin,
            tenant=self.tenant,
            username=self.admin.username,
            rol='ADMIN',
            activo=True,
        )
        self.token = _tenant_token_payload(
            self.identity_admin, self.tenant, self.admin,
        )['access']

    def tearDown(self):
        reset_current_tenant(self._tokens)
        self._force.__exit__(None, None, None)

    def test_precio_portal_auditado_en_bd_y_clave_tecnica_del_tenant(self):
        # El slug comercial puede diferir del tenant_key autoritativo.
        type(self.negocio).objects.using(ALIAS_A).filter(pk=self.negocio.pk).update(
            slug='nombre-comercial-distinto',
        )
        categoria = Categoria.objects.using(ALIAS_A).create(nombre='Catalogo fisico')
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')

        with patch(
            'apps.tenancy.authentication.configure_tenant_database',
            return_value=(self.tenant, ALIAS_A),
        ):
            alta = client.post('/api/v1/maestros/productos/', {
                'sku': 'A09-FISICO', 'nombre': 'Producto fisico',
                'precio_venta': '10.00', 'categoria': categoria.pk,
            }, format='json')
            self.assertEqual(alta.status_code, 201, alta.data)
            cambio = client.patch(
                f"/api/v1/maestros/productos/{alta.data['id']}/",
                {'precio_venta': '12.50'}, format='json',
            )
        self.assertEqual(cambio.status_code, 200, cambio.data)

        eventos = list(Auditoria.objects.using(ALIAS_A).filter(
            accion__startswith='productos.producto.', object_id=alta.data['id'],
        ).order_by('id'))
        self.assertEqual([evento.accion for evento in eventos], [
            'productos.producto.creado', 'productos.producto.precio_modificado',
        ])
        self.assertTrue(all(
            evento.tenant_key == self.tenant.tenant_key for evento in eventos
        ), [evento.tenant_key for evento in eventos])
        self.assertFalse(Auditoria.objects.using('default').filter(
            accion__startswith='productos.producto.',
        ).exists())

    def test_baja_revoca_membership_en_control_plane_desde_usuario_tenant(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        with patch(
            'apps.tenancy.authentication.configure_tenant_database',
            return_value=(self.tenant, ALIAS_A),
        ):
            alta = client.post(
                USUARIOS_URL,
                {
                    'username': 'cajera_fisica',
                    'email': 'cajera_fisica@example.test',
                    'password': 'A9!clave-segura-nueva',
                    'rol_asignacion': self.rol.pk,
                },
                format='json',
            )
        self.assertEqual(alta.status_code, 201, alta.data)
        usuario = Usuario.objects.using(ALIAS_A).get(pk=alta.data['id'])
        identity = Identity.objects.using('default').get(email=usuario.email)
        membership = Membership.objects.using('default').get(
            identity=identity, tenant=self.tenant, username=usuario.username,
        )

        with patch(
            'apps.tenancy.authentication.configure_tenant_database',
            return_value=(self.tenant, ALIAS_A),
        ):
            baja = client.delete(
                f'{USUARIOS_URL}{usuario.pk}/',
                {'motivo': 'Baja comprobada'},
                format='json',
            )
        self.assertEqual(baja.status_code, 204, baja.data)
        usuario.refresh_from_db(using=ALIAS_A)
        membership.refresh_from_db(using='default')
        self.assertFalse(usuario.activo)
        self.assertFalse(membership.activo)
        with self.assertRaises(AuthenticationFailed):
            _autorizar_tenant(
                identity=identity,
                tenant=self.tenant,
                username=usuario.username,
                impersonado=False,
            )
