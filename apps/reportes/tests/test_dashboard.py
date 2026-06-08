import json
from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import RequestFactory, TestCase, override_settings

from apps.reportes.views import api_metricas_hoy, dashboard
from apps.usuarios.models import Usuario
from apps.ventas.models import Pago, Venta


@override_settings(TIME_ZONE='America/Santo_Domingo', USE_TZ=True)
class DashboardReportesTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = Usuario.objects.create_user(
            username='admin',
            email='admin@test.local',
            password='test',
            rol='ADMIN',
            activo=True,
        )
        self.cajera = Usuario.objects.create_user(
            username='cajera',
            email='cajera@test.local',
            password='test',
            rol='CAJERA',
            activo=True,
        )
        self.otra_cajera = Usuario.objects.create_user(
            username='otra',
            email='otra@test.local',
            password='test',
            rol='CAJERA',
            activo=True,
        )
        self.local_night = datetime(
            2026, 5, 16, 23, 5, tzinfo=ZoneInfo('America/Santo_Domingo')
        )
        self.utc_next_day = datetime(2026, 5, 17, 3, 15, tzinfo=dt_timezone.utc)

    def crear_venta(self, usuario, total, metodo='EFECTIVO'):
        venta = Venta.objects.create(
            fecha_venta=self.local_night,
            usuario=usuario,
            subtotal=total,
            descuento_total=0,
            total=total,
            estado='COMPLETADA',
        )
        Pago.objects.create(venta=venta, metodo=metodo, monto=total)
        return venta

    @patch('django.utils.timezone.now')
    @patch('apps.reportes.views.render')
    def test_dashboard_usa_fecha_local_para_metricas_de_hoy(self, render_mock, now_mock):
        now_mock.return_value = self.utc_next_day
        self.crear_venta(self.admin, 7900, 'EFECTIVO')

        render_mock.side_effect = (
            lambda request, template, context: SimpleNamespace(
                status_code=200,
                template=template,
                context=context,
            )
        )

        request = self.factory.get('/reportes/')
        request.user = self.admin
        response = dashboard(request)

        self.assertEqual(response.context['fecha_hoy'].isoformat(), '2026-05-16')
        self.assertEqual(response.context['resumen_hoy']['cantidad'], 1)
        self.assertEqual(response.context['resumen_hoy']['total'], 7900)
        self.assertEqual(response.context['efectivo_hoy'], 7900)

    @patch('django.utils.timezone.now')
    def test_api_metricas_hoy_filtra_cajera_por_usuario(self, now_mock):
        now_mock.return_value = self.utc_next_day
        self.crear_venta(self.cajera, 1000, 'EFECTIVO')
        self.crear_venta(self.otra_cajera, 2000, 'TRANSFERENCIA')

        request = self.factory.get('/reportes/api/metricas-hoy/')
        request.user = self.cajera
        response = api_metricas_hoy(request)
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['cantidad_ventas'], 1)
        self.assertEqual(data['total_ventas'], 1000)
        self.assertEqual(data['efectivo'], 1000)
        self.assertEqual(data['transferencia'], 0)
