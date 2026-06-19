"""
Aislamiento multi-tenant de reportes cloud y sucursales-status (API-002).

  - Un usuario con negocio solo ve las sucursales de SU negocio en
    ventas-hoy / comparativo / ventas-por-cajero / sucursales-status.
  - SYSADMIN/global las ve todas.

(El blindaje de ventas-por-cajero ante ventas sin usuario, API-003, se prueba en
test_sync_venta_sin_usuario.py, que ejercita el handler de sync donde nace el caso.)
"""
from datetime import datetime, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.permisos import testing
from apps.sucursales.models import Sucursal
from apps.ventas.models import Venta

User = get_user_model()

PERMS_REPORTES = ['reportes.ver', 'reportes.consolidado.ver', 'sucursales.ver']


class ReportesScopeNegocioTests(TestCase):
    def setUp(self):
        self.neg_a = testing.crear_negocio('Negocio A')
        self.neg_b = testing.crear_negocio('Negocio B')

        self.user_a = User.objects.create_user(
            username='analista_a', email='ana_a@test.local', password='x',
            rol='CAJERA', activo=True,
        )
        testing.asignar(
            self.user_a, testing.crear_rol(self.neg_a, 'Analista', PERMS_REPORTES)
        )
        self.sysadmin = User.objects.create_user(
            username='sysadmin_rep', email='sys_rep@test.local', password='x',
            rol='SYSADMIN', activo=True,
        )

        self.suc_a = Sucursal.objects.create(
            codigo='A-001', nombre='Suc A', activa=True, negocio=self.neg_a,
            ultima_sync=timezone.now(),
        )
        self.suc_b = Sucursal.objects.create(
            codigo='B-001', nombre='Suc B', activa=True, negocio=self.neg_b,
            ultima_sync=timezone.now(),
        )

        self.cajero = User.objects.create_user(
            username='cajero_a', email='caj_a@test.local', password='x',
            rol='CAJERA', activo=True,
        )
        self.hoy = timezone.localdate()
        self.now_local = timezone.make_aware(
            datetime.combine(self.hoy, time(10, 0)),
            timezone.get_current_timezone(),
        )

        self._venta(self.suc_a, self.cajero, 'A-V1', '100.00')
        self._venta(self.suc_b, self.cajero, 'B-V1', '200.00')

    def _venta(self, sucursal, usuario, numero, total):
        return Venta.objects.create(
            numero_venta=numero, fecha_venta=self.now_local, usuario=usuario,
            sucursal=sucursal, subtotal=Decimal(total), total=Decimal(total),
            condicion_pago='CONTADO', estado='COMPLETADA',
        )

    def _api(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def _rango_hoy(self):
        return f'desde={self.hoy}&hasta={self.hoy}'

    def test_ventas_hoy_scoped_por_negocio(self):
        r = self._api(self.user_a).get('/api/v1/reportes/ventas-hoy/')
        self.assertEqual(r.status_code, 200)
        codigos = {s['sucursal_codigo'] for s in r.data['sucursales']}
        self.assertEqual(codigos, {'A-001'})

    def test_comparativo_scoped_por_negocio(self):
        r = self._api(self.user_a).get(f'/api/v1/reportes/comparativo/?{self._rango_hoy()}')
        self.assertEqual(r.status_code, 200)
        codigos = {s['sucursal_codigo'] for s in r.data['sucursales']}
        self.assertEqual(codigos, {'A-001'})

    def test_ventas_por_cajero_scoped_por_negocio(self):
        r = self._api(self.user_a).get(f'/api/v1/reportes/ventas-por-cajero/?{self._rango_hoy()}')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data['cajeros'])
        self.assertTrue(all(c['sucursal_codigo'] == 'A-001' for c in r.data['cajeros']))

    def test_sucursales_status_scoped_por_negocio(self):
        r = self._api(self.user_a).get('/api/v1/sucursales/status/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['resumen']['total_sucursales'], 1)
        self.assertEqual({s['codigo'] for s in r.data['sucursales']}, {'A-001'})

    def test_sysadmin_ve_todas_las_sucursales(self):
        r = self._api(self.sysadmin).get('/api/v1/sucursales/status/')
        self.assertEqual(r.data['resumen']['total_sucursales'], 2)
