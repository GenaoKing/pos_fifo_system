"""
Integracion de la conciliacion diaria (Fase 3) en el daemon `sincronizar`.

Se prueba `_conciliacion_diaria` de forma aislada, no el loop completo: el
loop entero necesita SYNC_ENABLED y un engine con conexion real, que es
justo lo que el resto de la suite de sync ya evita con el mismo criterio.
"""
from datetime import date, timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.sync.management.commands.sincronizar import Command
from apps.sync.models import LogSync


def _comando():
    cmd = Command()
    cmd.stdout = StringIO()
    cmd.stderr = StringIO()
    cmd._conciliacion_ultimo_dia = None
    cmd._conciliacion_reintento_desde = None
    return cmd


def _engine_conectado():
    engine = MagicMock()
    engine.check_connection.return_value = True
    return engine


class ConciliacionDiariaTests(TestCase):
    @override_settings(SYNC_CONCILIACION_ENABLED=True, SYNC_CONCILIACION_HORA=0)
    def test_corre_y_registra_logsync(self):
        cmd = _comando()
        engine = _engine_conectado()
        resultado = {
            'estado': 'OK', 'mensaje': 'Sin divergencias.', 'divergencias': [],
            'desde': date(2026, 8, 1), 'hasta': date(2026, 8, 2), 'tz': 'UTC', 'backfill': None,
        }
        with patch('apps.sync.conciliacion.conciliar', return_value=resultado):
            cmd._conciliacion_diaria(engine)

        self.assertEqual(LogSync.objects.filter(tipo='CONCILIACION').count(), 1)
        log = LogSync.objects.get(tipo='CONCILIACION')
        self.assertEqual(log.resultado, 'EXITOSO')

    @override_settings(SYNC_CONCILIACION_ENABLED=True, SYNC_CONCILIACION_HORA=0)
    def test_no_corre_dos_veces_el_mismo_dia(self):
        cmd = _comando()
        engine = _engine_conectado()
        resultado = {
            'estado': 'OK', 'mensaje': 'Sin divergencias.', 'divergencias': [],
            'desde': date(2026, 8, 1), 'hasta': date(2026, 8, 2), 'tz': 'UTC', 'backfill': None,
        }
        with patch('apps.sync.conciliacion.conciliar', return_value=resultado) as mock_conciliar:
            cmd._conciliacion_diaria(engine)
            cmd._conciliacion_diaria(engine)

        self.assertEqual(mock_conciliar.call_count, 1)

    @override_settings(SYNC_CONCILIACION_ENABLED=True, SYNC_CONCILIACION_HORA=23)
    def test_respeta_la_hora_minima_configurada(self):
        """A las 23h de hoy, si es antes de esa hora local, no corre todavia."""
        cmd = _comando()
        engine = _engine_conectado()
        if timezone.localtime().hour >= 23:
            self.skipTest('no se puede probar "todavia no es la hora" a las 23h+')

        with patch('apps.sync.conciliacion.conciliar') as mock_conciliar:
            cmd._conciliacion_diaria(engine)

        mock_conciliar.assert_not_called()

    @override_settings(SYNC_CONCILIACION_ENABLED=False)
    def test_deshabilitada_no_corre(self):
        cmd = _comando()
        engine = _engine_conectado()
        with patch('apps.sync.conciliacion.conciliar') as mock_conciliar:
            cmd._conciliacion_diaria(engine)
        mock_conciliar.assert_not_called()

    @override_settings(SYNC_CONCILIACION_ENABLED=True, SYNC_CONCILIACION_HORA=0)
    def test_una_excepcion_no_se_propaga(self):
        """El loop de push/pull no debe caerse porque la conciliacion falle."""
        cmd = _comando()
        engine = _engine_conectado()
        with patch('apps.sync.conciliacion.conciliar', side_effect=RuntimeError('boom')):
            cmd._conciliacion_diaria(engine)  # no debe lanzar

        self.assertEqual(LogSync.objects.filter(tipo='CONCILIACION').count(), 0)

    @override_settings(SYNC_CONCILIACION_ENABLED=True, SYNC_CONCILIACION_HORA=0)
    def test_sin_conexion_no_escanea_ni_consume_el_turno(self):
        """
        Un blip de conectividad a la hora de conciliacion no debe disparar un
        scan local completo ni consumir el turno del dia: el proximo ciclo
        (con conexion) tiene que poder conciliar de verdad.
        """
        cmd = _comando()
        engine = MagicMock()
        engine.check_connection.return_value = False

        with patch('apps.sync.conciliacion.conciliar') as mock_conciliar:
            cmd._conciliacion_diaria(engine)

        mock_conciliar.assert_not_called()
        self.assertIsNone(cmd._conciliacion_ultimo_dia)
        self.assertEqual(LogSync.objects.filter(tipo='CONCILIACION').count(), 0)

        # Vuelve la conexion en un ciclo posterior: si conciliar.
        engine.check_connection.return_value = True
        resultado = {
            'estado': 'OK', 'mensaje': 'Sin divergencias.', 'divergencias': [],
            'desde': date(2026, 8, 1), 'hasta': date(2026, 8, 2), 'tz': 'UTC', 'backfill': None,
        }
        with patch('apps.sync.conciliacion.conciliar', return_value=resultado) as mock_conciliar:
            cmd._conciliacion_diaria(engine)

        mock_conciliar.assert_called_once()
        self.assertIsNotNone(cmd._conciliacion_ultimo_dia)

    @override_settings(SYNC_CONCILIACION_ENABLED=True, SYNC_CONCILIACION_HORA=0)
    def test_error_no_consume_el_turno_y_activa_backoff(self):
        """
        Un ERROR real (cloud alcanzable, el resumen fallo) SI se registra en
        LogSync -- es señal legitima -- pero no consume el turno del dia, y
        activa un backoff de 1h para no re-escanear cada ciclo (60s) el resto
        del dia.
        """
        cmd = _comando()
        engine = _engine_conectado()
        resultado = {
            'estado': 'ERROR', 'mensaje': 'red: timeout', 'divergencias': [],
            'desde': date(2026, 8, 1), 'hasta': date(2026, 8, 2), 'tz': 'UTC', 'backfill': None,
        }
        with patch('apps.sync.conciliacion.conciliar', return_value=resultado) as mock_conciliar:
            cmd._conciliacion_diaria(engine)
            cmd._conciliacion_diaria(engine)  # backoff activo: no reintenta ya

        self.assertEqual(mock_conciliar.call_count, 1)
        self.assertIsNone(cmd._conciliacion_ultimo_dia)
        self.assertIsNotNone(cmd._conciliacion_reintento_desde)
        log = LogSync.objects.get(tipo='CONCILIACION')
        self.assertEqual(log.resultado, 'FALLO')

        # Pasada la hora de backoff, reintenta.
        cmd._conciliacion_reintento_desde = timezone.localtime() - timedelta(minutes=1)
        with patch('apps.sync.conciliacion.conciliar', return_value=resultado) as mock_conciliar:
            cmd._conciliacion_diaria(engine)

        mock_conciliar.assert_called_once()

    @override_settings(SYNC_CONCILIACION_ENABLED=True, SYNC_CONCILIACION_HORA=0)
    def test_pasa_el_engine_del_daemon_a_conciliar(self):
        """
        El engine ya construido por el daemon se reusa -- no se debe construir
        un SyncEngine() nuevo por separado dentro de `conciliar`.
        """
        cmd = _comando()
        engine = _engine_conectado()
        resultado = {
            'estado': 'OK', 'mensaje': 'Sin divergencias.', 'divergencias': [],
            'desde': date(2026, 8, 1), 'hasta': date(2026, 8, 2), 'tz': 'UTC', 'backfill': None,
        }
        with patch('apps.sync.conciliacion.conciliar', return_value=resultado) as mock_conciliar:
            cmd._conciliacion_diaria(engine)

        mock_conciliar.assert_called_once()
        _, kwargs = mock_conciliar.call_args
        self.assertIs(kwargs.get('engine'), engine)
