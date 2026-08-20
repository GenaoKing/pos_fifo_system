"""
Pull local de definiciones de rol: aplicar el payload del cloud actualiza
Rol.permisos e invalida el cache del motor (un cajero gana el permiso).
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.permisos import testing
from apps.permisos.catalogo import sembrar_catalogo
from apps.permisos.models import AsignacionRol, Permiso
from apps.sucursales.models import Sucursal
from apps.sync.engine import SyncEngine
from apps.sync.models import VersionMaestro

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
        self.assertIsNotNone(resultado['bloqueo'])

        cursor = VersionMaestro.objects.get(tabla='asignaciones')
        self.assertIsNone(cursor.ultima_version)
        self.assertIsNotNone(cursor.bloqueado_desde)

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

        # Ciclo 2: el cursor no avanzo, asi que la fila vuelve a bajar.
        mock_get.return_value = _Resp(payload)
        self.assertEqual(engine._pull_asignaciones()['count'], 1)
        self.assertTrue(
            AsignacionRol.objects.filter(usuario=usuario, rol=self.rol).exists()
        )

        cursor = VersionMaestro.objects.get(tabla='asignaciones')
        self.assertIsNone(cursor.bloqueado_desde)
