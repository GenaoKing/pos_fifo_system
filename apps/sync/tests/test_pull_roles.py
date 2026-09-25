"""
Pull local de definiciones de rol: aplicar el payload del cloud actualiza
Rol.permisos e invalida el cache del motor (un cajero gana el permiso).
"""
from unittest.mock import patch
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.permisos import testing
from apps.permisos.catalogo import sembrar_catalogo
from apps.permisos.models import AsignacionRol, Permiso
from apps.sucursales.models import Sucursal
from apps.sync.engine import SyncEngine
from apps.sync.models import DiferidoSync, VersionMaestro
from apps.tenancy.context import reset_current_tenant, set_current_tenant

User = get_user_model()


class _Resp:
    status_code = 200
    text = ''

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@override_settings(SUCURSAL_CODIGO='SD-001')
class PullRolesTests(TestCase):
    def setUp(self):
        sembrar_catalogo(Permiso)
        self.negocio = testing.crear_negocio('Royal Plast')
        self.sucursal = Sucursal.objects.create(
            codigo='SD-001', nombre='SD', activa=True, negocio=self.negocio
        )
        # Rol Cajero local SIN compras.registrar, asignado a un cajero.
        self.rol = testing.crear_rol(self.negocio, 'Cajero', ['clientes.ver'])
        self.cajero = User.objects.create_user('caja', 'c@e.com', 'x', rol='CAJERA')
        testing.asignar(self.cajero, self.rol)

    @patch('apps.sync.engine.requests.get')
    def test_pull_actualiza_permisos_e_invalida_cache(self, mock_get):
        # Estado inicial (cachea el set sin compras.registrar).
        self.assertFalse(self.cajero.tiene_permiso('compras.registrar'))

        # El cloud devuelve el mismo rol pero ahora CON compras.registrar.
        payload = [{
            'slug': 'cajero',
            'nombre': 'Cajero',
            'activo': True,
            'permisos': ['clientes.ver', 'compras.registrar'],
            'fecha_modificacion': timezone.now().isoformat(),
        }]
        mock_get.return_value = _Resp(payload)

        engine = SyncEngine(cloud_url='https://cloud.example', token='t')
        n = engine._pull_roles()['count']

        self.assertEqual(n, 1)
        # La signal m2m invalido el cache → el cajero ahora SI puede.
        self.assertTrue(self.cajero.tiene_permiso('compras.registrar'))

    @patch('apps.sync.engine.requests.get')
    def test_sin_negocio_no_hace_nada(self, mock_get):
        self.sucursal.negocio = None
        self.sucursal.save()
        from django.core.cache import cache
        cache.clear()  # limpia el cache de get_sucursal_actual

        engine = SyncEngine(cloud_url='https://cloud.example', token='t')
        self.assertEqual(engine._pull_roles()['count'], 0)
        mock_get.assert_not_called()

    @patch('apps.sync.engine.requests.get')
    def test_v2_aplica_baja_antes_de_permiso_desconocido(self, mock_get):
        remoto = uuid.uuid4()
        payload = {
            'schema_version': 'rbac.sync.v2',
            'snapshot_complete': True,
            'tenant_key': self.negocio.slug,
            'roles': [{
                'cloud_id': str(remoto),
                'revision': 7,
                'slug': self.rol.slug,
                'nombre': self.rol.nombre,
                'active': False,
                'permission_codes': ['permiso.nuevo.que.el.pos.no.conoce'],
                'deleted_at': timezone.now().isoformat(),
                'fecha_modificacion': timezone.now().isoformat(),
            }],
        }
        mock_get.return_value = _Resp(payload)

        resultado = SyncEngine(
            cloud_url='https://cloud.example', token='t',
        )._pull_roles()

        self.rol.refresh_from_db()
        self.assertTrue(resultado['ok'])
        self.assertIsNone(resultado['bloqueo'])
        self.assertFalse(self.rol.activo)
        self.assertEqual(self.rol.cloud_id, remoto)
        self.assertEqual(self.rol.revision, 7)
        self.assertEqual(
            mock_get.call_args.kwargs['headers']['X-RBAC-Schema'],
            'rbac.sync.v2',
        )

    @patch('apps.sync.engine.requests.get')
    def test_snapshot_completo_revoca_solo_filas_de_propiedad_cloud(self, mock_get):
        self.rol.origen_cloud = True
        self.rol.save(update_fields=['origen_cloud', 'fecha_modificacion'])
        local = testing.crear_rol(self.negocio, 'Solo local', ['clientes.ver'])
        mock_get.return_value = _Resp({
            'schema_version': 'rbac.sync.v2',
            'snapshot_complete': True,
            'tenant_key': self.negocio.slug,
            'roles': [],
        })

        SyncEngine(cloud_url='https://cloud.example', token='t')._pull_roles()

        self.rol.refresh_from_db()
        local.refresh_from_db()
        self.assertFalse(self.rol.activo)
        self.assertTrue(local.activo)

    @patch('apps.sync.engine.requests.get')
    def test_snapshot_parcial_no_revoca_ausentes(self, mock_get):
        self.rol.origen_cloud = True
        self.rol.save(update_fields=['origen_cloud', 'fecha_modificacion'])
        mock_get.return_value = _Resp({
            'schema_version': 'rbac.sync.v2',
            'snapshot_complete': False,
            'tenant_key': self.negocio.slug,
            'roles': [],
        })

        SyncEngine(cloud_url='https://cloud.example', token='t')._pull_roles()

        self.rol.refresh_from_db()
        self.assertTrue(self.rol.activo)

    @patch('apps.sync.engine.requests.get')
    def test_snapshot_de_otro_tenant_no_se_aplica_ni_revoca(self, mock_get):
        self.rol.origen_cloud = True
        self.rol.save(update_fields=['origen_cloud', 'fecha_modificacion'])
        mock_get.return_value = _Resp({
            'schema_version': 'rbac.sync.v2',
            'snapshot_complete': True,
            'tenant_key': 'otro-negocio',
            'roles': [],
        })

        resultado = SyncEngine(
            cloud_url='https://cloud.example', token='t',
        )._pull_roles()

        self.rol.refresh_from_db()
        self.assertTrue(self.rol.activo)
        self.assertIn('envelope invalido', resultado['bloqueo'])

    @patch('apps.sync.engine.requests.get')
    def test_envelope_usa_tenant_key_tecnico_del_contexto(self, mock_get):
        self.rol.origen_cloud = True
        self.rol.save(update_fields=['origen_cloud', 'fecha_modificacion'])
        mock_get.return_value = _Resp({
            'schema_version': 'rbac.sync.v2',
            'snapshot_complete': True,
            'tenant_key': 'tenant_tecnico',
            'roles': [],
        })

        tokens = set_current_tenant('tenant_tecnico', 'tnt_tenant_tecnico')
        try:
            resultado = SyncEngine(
                cloud_url='https://cloud.example', token='t',
            )._pull_roles()
        finally:
            reset_current_tenant(tokens)

        self.rol.refresh_from_db()
        self.assertTrue(resultado['ok'])
        self.assertFalse(self.rol.activo)


@override_settings(SUCURSAL_CODIGO='SD-001')
class PullAsignacionesTests(TestCase):
    def setUp(self):
        sembrar_catalogo(Permiso)
        self.negocio = testing.crear_negocio('Royal Plast')
        self.sucursal = Sucursal.objects.create(
            codigo='SD-001', nombre='SD', activa=True, negocio=self.negocio
        )
        self.rol = testing.crear_rol(self.negocio, 'Compras', ['compras.registrar'])
        self.cajero = User.objects.create_user(
            'caja_asig', 'ca@e.com', 'x', rol='CAJERA', negocio=self.negocio
        )

    @patch('apps.sync.engine.requests.get')
    def test_pull_crea_asignacion_y_activa_permiso(self, mock_get):
        self.assertFalse(self.cajero.tiene_permiso('compras.registrar'))

        payload = [{
            'usuario_username': 'caja_asig',
            'rol_slug': 'compras',
            'sucursal_codigo': None,
            'activo': True,
            'fecha_modificacion': timezone.now().isoformat(),
        }]
        mock_get.return_value = _Resp(payload)

        engine = SyncEngine(cloud_url='https://cloud.example', token='t')
        n = engine._pull_asignaciones()['count']

        self.assertEqual(n, 1)
        self.assertTrue(
            AsignacionRol.objects.filter(usuario=self.cajero, rol=self.rol).exists()
        )
        self.assertTrue(self.cajero.tiene_permiso('compras.registrar'))

    @patch('apps.sync.engine.requests.get')
    def test_username_cloud_con_mayusculas_usa_identidad_local_canonica(self, mock_get):
        """La cuenta de servicio STG-01 se crea localmente en minusculas."""
        servicio = User.objects.create_service_user(
            'sucursal_service_STG-01', 'servicio@a09.local',
            rol='CAJERA', negocio=self.negocio, activo=True,
        )
        self.assertEqual(servicio.username, 'sucursal_service_stg-01')
        marca = timezone.now()
        payload = {
            'usuario_username': 'sucursal_service_STG-01',
            'rol_slug': 'compras',
            'sucursal_codigo': None,
            'activo': True,
            'fecha_modificacion': marca.isoformat(),
        }
        mock_get.return_value = _Resp([payload])
        engine = SyncEngine(cloud_url='https://cloud.example', token='t')
        resultado = engine._pull_asignaciones()
        self.assertEqual(resultado['count'], 1)
        self.assertEqual(resultado['diferidos_pendientes'], 0)
        asignacion = AsignacionRol.objects.get(usuario=servicio, rol=self.rol)
        self.assertTrue(asignacion.activo)

        payload['activo'] = False
        payload['fecha_modificacion'] = (marca + timedelta(seconds=1)).isoformat()
        mock_get.return_value = _Resp([payload])
        baja = engine._pull_asignaciones()
        asignacion.refresh_from_db()
        self.assertFalse(asignacion.activo, baja)

    @patch('apps.sync.engine.requests.get')
    def test_pull_difiere_usuario_inexistente_sin_darlo_por_aplicado(self, mock_get):
        """
        SYNC-006. Antes esto contaba como aplicado (`count == 1`) y el cursor
        avanzaba: la asignacion no volvia a bajar nunca, porque en el cloud esa
        fila no cambiaba y el `?desde=` ya la habia dejado atras. El usuario
        quedaba sin sus permisos de forma permanente.
        """
        payload = [{
            'usuario_username': 'no_existe',
            'rol_slug': 'compras',
            'sucursal_codigo': None,
            'activo': True,
            'fecha_modificacion': timezone.now().isoformat(),
        }]
        mock_get.return_value = _Resp(payload)

        engine = SyncEngine(cloud_url='https://cloud.example', token='t')
        resultado = engine._pull_asignaciones()

        self.assertEqual(resultado['count'], 0)
        self.assertEqual(AsignacionRol.objects.count(), 0)
        self.assertIsNone(resultado['bloqueo'])
        self.assertEqual(resultado['diferidos_pendientes'], 1)

        cursor = VersionMaestro.objects.get(tabla='asignaciones')
        self.assertIsNotNone(cursor.ultima_version)
        self.assertIsNone(cursor.bloqueado_desde)
        self.assertEqual(
            DiferidoSync.objects.get(tabla='asignaciones').estado,
            'PENDIENTE',
        )

    @patch('apps.sync.engine.requests.get')
    def test_asignacion_diferida_se_aplica_cuando_llega_la_dependencia(self, mock_get):
        """Convergencia: el mismo payload, sin cambiar en cloud, se aplica en un
        ciclo posterior una vez que el usuario existe localmente."""
        User = get_user_model()
        payload = [{
            'usuario_username': 'cajero_tardio',
            'rol_slug': 'compras',
            'sucursal_codigo': None,
            'activo': True,
            'fecha_modificacion': timezone.now().isoformat(),
        }]
        mock_get.return_value = _Resp(payload)
        engine = SyncEngine(cloud_url='https://cloud.example', token='t')

        # Ciclo 1: el usuario todavia no existe.
        self.assertEqual(engine._pull_asignaciones()['count'], 0)
        self.assertEqual(AsignacionRol.objects.count(), 0)

        # El usuario aparece (alta local, provision, lo que sea).
        usuario = User.objects.create_user(
            username='cajero_tardio',
            email='cajero_tardio@test.local',
            password='pass',
            rol='CAJERA',
            activo=True,
        )
        usuario.negocio = self.negocio
        usuario.save(update_fields=['negocio'])

        # Ciclo 2: la fila no vuelve a bajar; se recupera desde la cola durable.
        mock_get.return_value = _Resp([])
        resultado = engine._pull_asignaciones()
        self.assertEqual(resultado['count'], 1)
        self.assertEqual(resultado['diferidos_resueltos'], 1)
        self.assertTrue(
            AsignacionRol.objects.filter(usuario=usuario, rol=self.rol).exists()
        )
        self.assertEqual(DiferidoSync.objects.get().estado, 'RESUELTO')

        cursor = VersionMaestro.objects.get(tabla='asignaciones')
        self.assertIsNone(cursor.bloqueado_desde)

    @patch('apps.sync.engine.requests.get')
    def test_baja_v2_no_depende_de_usuario_rol_o_permiso_conocido(self, mock_get):
        asignacion = testing.asignar(self.cajero, self.rol, set_negocio=False)
        asignacion.origen_cloud = True
        asignacion.revision = 3
        asignacion.save(
            update_fields=['origen_cloud', 'revision', 'fecha_modificacion'],
            _preserve_rbac_revision=True,
        )
        payload = {
            'schema_version': 'rbac.sync.v2',
            'snapshot_complete': True,
            'tenant_key': self.negocio.slug,
            'scope': {'branch_code': self.sucursal.codigo},
            'assignments': [{
                'cloud_id': str(asignacion.cloud_id),
                'revision': 4,
                'usuario_username': 'usuario_ya_borrado',
                'rol_slug': 'rol-que-este-pos-no-conoce',
                'sucursal_codigo': None,
                'active': False,
                'deleted_at': timezone.now().isoformat(),
                'fecha_modificacion': timezone.now().isoformat(),
            }],
        }
        mock_get.return_value = _Resp(payload)

        resultado = SyncEngine(
            cloud_url='https://cloud.example', token='t',
        )._pull_asignaciones()

        asignacion.refresh_from_db()
        self.assertTrue(resultado['ok'])
        self.assertIsNone(resultado['bloqueo'])
        self.assertFalse(asignacion.activo)
        self.assertEqual(asignacion.revision, 4)

    @patch('apps.sync.engine.requests.get')
    def test_snapshot_completo_no_revoca_asignacion_local(self, mock_get):
        cloud = testing.asignar(self.cajero, self.rol, set_negocio=False)
        cloud.origen_cloud = True
        cloud.save(update_fields=['origen_cloud', 'fecha_modificacion'])
        otro_rol = testing.crear_rol(self.negocio, 'Local', ['clientes.ver'])
        local = testing.asignar(self.cajero, otro_rol, set_negocio=False)
        mock_get.return_value = _Resp({
            'schema_version': 'rbac.sync.v2',
            'snapshot_complete': True,
            'tenant_key': self.negocio.slug,
            'scope': {'branch_code': self.sucursal.codigo},
            'assignments': [],
        })

        SyncEngine(
            cloud_url='https://cloud.example', token='t',
        )._pull_asignaciones()

        cloud.refresh_from_db()
        local.refresh_from_db()
        self.assertFalse(cloud.activo)
        self.assertTrue(local.activo)

    @patch('apps.sync.engine.requests.get')
    def test_revision_menor_se_ignora_y_no_revoca(self, mock_get):
        asignacion = testing.asignar(self.cajero, self.rol, set_negocio=False)
        asignacion.origen_cloud = True
        asignacion.revision = 9
        asignacion.save(
            update_fields=['origen_cloud', 'revision', 'fecha_modificacion'],
            _preserve_rbac_revision=True,
        )
        mock_get.return_value = _Resp({
            'schema_version': 'rbac.sync.v2',
            'snapshot_complete': False,
            'tenant_key': self.negocio.slug,
            'scope': {'branch_code': self.sucursal.codigo},
            'assignments': [{
                'cloud_id': str(asignacion.cloud_id),
                'revision': 8,
                'usuario_username': self.cajero.username,
                'rol_slug': self.rol.slug,
                'sucursal_codigo': None,
                'active': False,
                'fecha_modificacion': timezone.now().isoformat(),
            }],
        })

        SyncEngine(
            cloud_url='https://cloud.example', token='t',
        )._pull_asignaciones()

        asignacion.refresh_from_db()
        self.assertTrue(asignacion.activo)
        self.assertEqual(asignacion.revision, 9)
