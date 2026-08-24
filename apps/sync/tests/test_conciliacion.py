"""
`apps/sync/conciliacion.py` y el comando `conciliar` — Fase 3.

El cloud se simula parcheando `SyncEngine.obtener_resumen` directamente
(mismo nivel que el resto de la suite de sync parchea requests), asi no hace
falta levantar servidor ni depender del formato exacto de `requests.Response`.
"""
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.sucursales.models import Sucursal
from apps.sync.conciliacion import conciliar, ventana_conciliacion
from apps.sync.engine import SyncEngine
from apps.sync.models import LogSync
from apps.ventas.models import Venta

User = get_user_model()


class VentanaConciliacionTests(TestCase):
    def test_excluye_el_dia_en_curso(self):
        desde, hasta = ventana_conciliacion(7)
        self.assertEqual(hasta, timezone.localdate() - timedelta(days=1))
        self.assertEqual(desde, timezone.localdate() - timedelta(days=7))


class ConciliarTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            username='cajera_conc', email='cc@test.local', password='x', rol='CAJERA',
        )
        self.sucursal = Sucursal.objects.create(codigo='CN-001', nombre='CN', activa=True)
        self.engine = SyncEngine(cloud_url='https://cloud.example', token='tok')

    def _venta(self, numero, fecha_venta, total='100.00'):
        return Venta.objects.create(
            numero_venta=numero, fecha_venta=fecha_venta, usuario=self.usuario,
            sucursal=self.sucursal, total=Decimal(total), estado='COMPLETADA',
        )

    def test_sin_divergencias_da_ok(self):
        ayer = timezone.now() - timedelta(days=1)
        self._venta('V-C-1', ayer, total='100.00')

        dia = timezone.localtime(ayer).date().isoformat()
        resumen_cloud = {'ventas': {dia: {'count': 1, 'suma': '100.00', 'anuladas': 0, 'max_ref': 'V-C-1'}}}

        with patch.object(self.engine, 'obtener_resumen', return_value=(resumen_cloud, None)):
            resultado = conciliar(dias=7, engine=self.engine)

        self.assertEqual(resultado['estado'], 'OK')
        self.assertEqual(resultado['divergencias'], [])

    def test_con_divergencias_da_divergente_y_las_detalla(self):
        ayer = timezone.now() - timedelta(days=1)
        self._venta('V-C-1', ayer, total='100.00')
        self._venta('V-C-2', ayer, total='200.00')

        with patch.object(self.engine, 'obtener_resumen', return_value=({'ventas': {}}, None)):
            resultado = conciliar(dias=7, engine=self.engine)

        self.assertEqual(resultado['estado'], 'DIVERGENTE')
        self.assertTrue(resultado['divergencias'])
        self.assertIsNone(resultado['backfill'], 'sin --backfill no debe correr nada')

    def test_cloud_sin_endpoint_degrada_a_no_soportado(self):
        with patch.object(self.engine, 'obtener_resumen', return_value=(None, 'no_soportado')):
            resultado = conciliar(dias=7, engine=self.engine)

        self.assertEqual(resultado['estado'], 'NO_SOPORTADO')
        self.assertIn('Fase 3', resultado['mensaje'])

    def test_error_de_red_no_revienta(self):
        with patch.object(self.engine, 'obtener_resumen', return_value=(None, 'red: timeout')):
            resultado = conciliar(dias=7, engine=self.engine)

        self.assertEqual(resultado['estado'], 'ERROR')
        self.assertEqual(resultado['mensaje'], 'red: timeout')

    def test_backfill_delega_en_verificar_sync_via_call_command(self):
        ayer = timezone.now() - timedelta(days=1)
        self._venta('V-C-1', ayer, total='100.00')

        with patch.object(self.engine, 'obtener_resumen', return_value=({'ventas': {}}, None)):
            with patch('apps.sync.conciliacion.call_command') as mock_call:
                resultado = conciliar(dias=7, engine=self.engine, backfill=True, ejecutar=True)

        self.assertIsNotNone(resultado['backfill'])
        mock_call.assert_called_once()
        args, kwargs = mock_call.call_args
        self.assertEqual(args[0], 'verificar_sync')
        self.assertTrue(kwargs.get('backfill'))
        self.assertTrue(kwargs.get('ejecutar'))

    def test_backfill_no_corre_si_no_hay_divergencias(self):
        ayer = timezone.now() - timedelta(days=1)
        self._venta('V-C-1', ayer, total='100.00')
        dia = timezone.localtime(ayer).date().isoformat()
        resumen_cloud = {'ventas': {dia: {'count': 1, 'suma': '100.00', 'anuladas': 0, 'max_ref': 'V-C-1'}}}

        with patch.object(self.engine, 'obtener_resumen', return_value=(resumen_cloud, None)):
            with patch('apps.sync.conciliacion.call_command') as mock_call:
                conciliar(dias=7, engine=self.engine, backfill=True, ejecutar=True)

        mock_call.assert_not_called()


class ConciliarCommandTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            username='cajera_conc_cmd', email='ccc@test.local', password='x', rol='CAJERA',
        )
        self.sucursal = Sucursal.objects.create(codigo='CN-002', nombre='CN2', activa=True)

    def test_comando_json_reporta_ok(self):
        with patch('apps.sync.engine.SyncEngine.obtener_resumen', return_value=({'ventas': {}}, None)):
            salida = StringIO()
            call_command('conciliar', '--dias=7', '--json', stdout=salida)

        self.assertIn('"estado": "OK"', salida.getvalue())

    def test_comando_no_soportado_no_lanza_error(self):
        with patch('apps.sync.engine.SyncEngine.obtener_resumen', return_value=(None, 'no_soportado')):
            salida = StringIO()
            call_command('conciliar', stdout=salida)  # no debe lanzar SystemExit

        self.assertIn('Fase 3', salida.getvalue())

    def test_comando_error_sale_con_codigo_no_cero(self):
        with patch('apps.sync.engine.SyncEngine.obtener_resumen', return_value=(None, 'red: boom')):
            with self.assertRaises(SystemExit):
                call_command('conciliar', stdout=StringIO())
