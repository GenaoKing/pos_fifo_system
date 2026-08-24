"""
Integracion de la conciliacion diaria (Fase 3) en el daemon `sincronizar`.

Se prueba `_conciliacion_diaria` de forma aislada, no el loop completo: el
loop entero necesita SYNC_ENABLED y un engine con conexion real, que es
justo lo que el resto de la suite de sync ya evita con el mismo criterio.
"""
from datetime import date
from io import StringIO
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.sync.management.commands.sincronizar import Command
from apps.sync.models import LogSync


def _comando():
    cmd = Command()
    cmd.stdout = StringIO()
    cmd.stderr = StringIO()
    cmd._conciliacion_ultimo_dia = None
    return cmd


class ConciliacionDiariaTests(TestCase):
    @override_settings(SYNC_CONCILIACION_ENABLED=True, SYNC_CONCILIACION_HORA=0)
    def test_corre_y_registra_logsync(self):
        cmd = _comando()
        resultado = {
            'estado': 'OK', 'mensaje': 'Sin divergencias.', 'divergencias': [],
            'desde': date(2026, 8, 1), 'hasta': date(2026, 8, 2), 'tz': 'UTC', 'backfill': None,
        }
        with patch('apps.sync.conciliacion.conciliar', return_value=resultado):
            cmd._conciliacion_diaria(engine=None)

        self.assertEqual(LogSync.objects.filter(tipo='CONCILIACION').count(), 1)
        log = LogSync.objects.get(tipo='CONCILIACION')
        self.assertEqual(log.resultado, 'EXITOSO')

    @override_settings(SYNC_CONCILIACION_ENABLED=True, SYNC_CONCILIACION_HORA=0)
    def test_no_corre_dos_veces_el_mismo_dia(self):
        cmd = _comando()
        resultado = {
            'estado': 'OK', 'mensaje': 'Sin divergencias.', 'divergencias': [],
            'desde': date(2026, 8, 1), 'hasta': date(2026, 8, 2), 'tz': 'UTC', 'backfill': None,
        }
        with patch('apps.sync.conciliacion.conciliar', return_value=resultado) as mock_conciliar:
            cmd._conciliacion_diaria(engine=None)
            cmd._conciliacion_diaria(engine=None)

        self.assertEqual(mock_conciliar.call_count, 1)

    @override_settings(SYNC_CONCILIACION_ENABLED=True, SYNC_CONCILIACION_HORA=23)
    def test_respeta_la_hora_minima_configurada(self):
        """A las 23h de hoy, si es antes de esa hora local, no corre todavia."""
        from django.utils import timezone

        cmd = _comando()
        if timezone.localtime().hour >= 23:
            self.skipTest('no se puede probar "todavia no es la hora" a las 23h+')

        with patch('apps.sync.conciliacion.conciliar') as mock_conciliar:
            cmd._conciliacion_diaria(engine=None)

        mock_conciliar.assert_not_called()

    @override_settings(SYNC_CONCILIACION_ENABLED=False)
    def test_deshabilitada_no_corre(self):
        cmd = _comando()
        with patch('apps.sync.conciliacion.conciliar') as mock_conciliar:
            cmd._conciliacion_diaria(engine=None)
        mock_conciliar.assert_not_called()

    @override_settings(SYNC_CONCILIACION_ENABLED=True, SYNC_CONCILIACION_HORA=0)
    def test_una_excepcion_no_se_propaga(self):
        """El loop de push/pull no debe caerse porque la conciliacion falle."""
        cmd = _comando()
        with patch('apps.sync.conciliacion.conciliar', side_effect=RuntimeError('boom')):
            cmd._conciliacion_diaria(engine=None)  # no debe lanzar

        self.assertEqual(LogSync.objects.filter(tipo='CONCILIACION').count(), 0)
