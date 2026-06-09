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
from apps.permisos.models import Permiso
from apps.sucursales.models import Sucursal
from apps.sync.engine import SyncEngine

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
        n = engine._pull_roles()

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
        self.assertEqual(engine._pull_roles(), 0)
        mock_get.assert_not_called()
