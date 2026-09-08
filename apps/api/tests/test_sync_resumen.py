"""
Endpoint cloud GET /api/v1/sync/resumen/ — Fase 3 (anti-entropia).
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.sucursales.models import Sucursal
from apps.ventas.models import Venta

User = get_user_model()


class SyncResumenEndpointTests(TestCase):
    url = '/api/v1/sync/resumen/'

    def setUp(self):
        self.svc = User.objects.create_user(
            'svc_resumen', 'svc_resumen@test.local', 'x', rol='CAJERA',
        )
        self.sucursal = Sucursal.objects.create(
            codigo='RS-RES-001', nombre='RS Resumen', activa=True, usuario_servicio=self.svc,
        )
        self.token = Token.objects.create(user=self.svc)

    def _api(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        return client

    def test_requiere_autenticacion_de_sucursal(self):
        r = APIClient().get(self.url, {'desde': '2026-08-01', 'hasta': '2026-08-02', 'tz': 'UTC'})
        self.assertEqual(r.status_code, 401)

    def test_faltan_parametros_da_400(self):
        r = self._api().get(self.url, {'desde': '2026-08-01'})
        self.assertEqual(r.status_code, 400)

    def test_tz_invalida_da_400(self):
        r = self._api().get(
            self.url, {'desde': '2026-08-01', 'hasta': '2026-08-02', 'tz': 'no/existe'},
        )
        self.assertEqual(r.status_code, 400)

    def test_hasta_anterior_a_desde_da_400(self):
        r = self._api().get(
            self.url, {'desde': '2026-08-05', 'hasta': '2026-08-01', 'tz': 'UTC'},
        )
        self.assertEqual(r.status_code, 400)

    def test_devuelve_agregados_escopados_a_la_sucursal_del_token(self):
        otra = Sucursal.objects.create(codigo='RS-RES-002', nombre='RS Otra', activa=True)
        tz = timezone.get_current_timezone()
        fecha = timezone.make_aware(timezone.datetime(2026, 8, 18, 10, 0), tz)

        Venta.objects.create(
            numero_venta='V-RES-1', fecha_venta=fecha, usuario=self.svc,
            sucursal=self.sucursal, total=Decimal('100.00'), estado='COMPLETADA',
        )
        # De OTRA sucursal: no debe contar en el resumen de esta.
        Venta.objects.create(
            numero_venta='V-RES-2', fecha_venta=fecha, usuario=self.svc,
            sucursal=otra, total=Decimal('999.00'), estado='COMPLETADA',
        )

        r = self._api().get(
            self.url, {'desde': '2026-08-18', 'hasta': '2026-08-18', 'tz': 'America/Santo_Domingo'},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['ventas']['2026-08-18']['count'], 1)
        self.assertEqual(r.data['ventas']['2026-08-18']['suma'], '100.00')
        self.assertEqual(r.data['sucursal_codigo'], 'RS-RES-001')

    def test_frontera_de_medianoche_en_el_endpoint(self):
        from datetime import timezone as dt_timezone

        fecha_utc = timezone.datetime(2026, 8, 19, 3, 30, tzinfo=dt_timezone.utc)  # 23:30 RD del dia 18
        Venta.objects.create(
            numero_venta='V-RES-FRONTERA', fecha_venta=fecha_utc, usuario=self.svc,
            sucursal=self.sucursal, total=Decimal('50.00'), estado='COMPLETADA',
        )

        r_local = self._api().get(
            self.url, {'desde': '2026-08-18', 'hasta': '2026-08-18', 'tz': 'America/Santo_Domingo'},
        )
        self.assertEqual(r_local.data['ventas']['2026-08-18']['count'], 1)

        r_utc = self._api().get(
            self.url, {'desde': '2026-08-18', 'hasta': '2026-08-18', 'tz': 'UTC'},
        )
        self.assertEqual(r_utc.data.get('ventas', {}), {}, 'en UTC la venta cae el dia 19, no el 18')
