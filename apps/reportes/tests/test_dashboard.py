import json
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from apps.inventario.models import Lote
from apps.permisos import testing as permisos_testing
from apps.productos.models import Categoria, Producto
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
        # El dashboard ahora exige `reportes.ver` (RPT-014). En una instalacion
        # real llega por `PERMISOS_CAJERO_DEFAULT`; aca hay que darlo explicito
        # porque el fixture arma los usuarios a mano.
        for usuario in (self.cajera, self.otra_cajera):
            permisos_testing.habilitar_cajero(
                usuario, permisos=['ventas.crear', 'reportes.ver'],
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

    @patch('django.utils.timezone.now')
    @patch('apps.reportes.views.render')
    def test_dashboard_no_crashea_con_productos_bajo_stock(self, render_mock, now_mock):
        """
        Regresion: la anotacion `stock_actual` en el queryset de productos
        bajo stock colisiona con la @property de solo lectura del mismo
        nombre en `Producto`. El ORM crashea al hidratar la fila
        (`AttributeError: property 'stock_actual' of 'Producto' object has
        no setter`) apenas hay al menos un producto por debajo del minimo.
        """
        now_mock.return_value = self.utc_next_day

        categoria = Categoria.objects.create(nombre='Reportes Dashboard Test')
        producto = Producto.objects.create(
            sku='RPT-BAJO-STOCK', nombre='Bajo stock', categoria=categoria,
            precio_venta=Decimal('10.00'), stock_minimo=10,
        )
        Lote.objects.create(
            producto=producto, numero_lote='RPT-LOTE-1',
            fecha_compra=timezone.now(), cantidad_inicial=5, cantidad_actual=5,
            costo_unitario=Decimal('5.00'), activo=True,
        )

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

        self.assertEqual(response.status_code, 200)
        items = response.context['productos_bajo_stock']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['producto'], producto)
        self.assertEqual(items[0]['stock_actual'], 5)
        self.assertEqual(items[0]['stock_minimo'], 10)
