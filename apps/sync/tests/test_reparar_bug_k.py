"""Matriz del comando BUG-K: dry-run por defecto y reparacion dirigida."""
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.sucursales.models import Sucursal
from apps.sync.management.commands.reparar_bug_k import Command
from apps.sync.models import EventoSync, LogSync


@override_settings(
    SUCURSAL_CODIGO='CMD-A04',
    CLOUD_API_URL='https://cloud.test',
    CLOUD_API_TOKEN='token-test',
)
class RepararBugKTests(TestCase):
    def setUp(self):
        cache.clear()
        self.sucursal = Sucursal.objects.create(
            codigo='CMD-A04', nombre='Comando A04', activa=True,
        )
        self.evento = EventoSync.objects.create(
            sucursal=self.sucursal,
            tipo_evento='VENTA_CREADA',
            objeto_referencia='V-BUG-K-1',
            payload={
                'numero_venta': 'V-BUG-K-1',
                'sucursal_codigo': self.sucursal.codigo,
            },
            hash_payload='bug-k-confirmado',
            estado='CONFIRMADO',
        )

    def tearDown(self):
        cache.clear()

    def _respuesta_cloud(self, clasificacion='FALSO_ACK'):
        return {
            'schema_version': 'sync.reconciliation.v1',
            'scope': {
                'tenant_key': '',
                'branch_code': self.sucursal.codigo,
                'branch_ref': str(self.sucursal.pk),
            },
            'resultados': [{
                'event_id': str(self.evento.event_id),
                'hash': self.evento.hash_payload,
                'tipo_evento': self.evento.tipo_evento,
                'objeto_referencia': self.evento.objeto_referencia,
                'clasificacion': clasificacion,
                'accion': (
                    'REENVIAR_DIRIGIDO'
                    if clasificacion == 'FALSO_ACK'
                    else 'REVISION_MANUAL'
                ),
            }],
        }

    @patch('apps.sync.engine.SyncEngine.consultar_reconciliacion_eventos')
    def test_dry_run_lista_exacta_sin_cambiar_el_evento(self, consultar):
        consultar.return_value = self._respuesta_cloud()
        salida = io.StringIO()

        call_command('reparar_bug_k', json=True, stdout=salida)

        plan = json.loads(salida.getvalue())
        self.assertTrue(plan['dry_run'])
        self.assertEqual(plan['scope']['branch_code'], self.sucursal.codigo)
        self.assertEqual(plan['resumen']['FALSO_ACK'], 1)
        self.assertEqual(len(plan['acciones']), 1)
        self.assertEqual(
            plan['acciones'][0]['event_id'], str(self.evento.event_id),
        )
        self.evento.refresh_from_db()
        self.assertEqual(self.evento.estado, 'CONFIRMADO')
        self.assertEqual(LogSync.objects.count(), 0)

    @patch('apps.sync.engine.SyncEngine.consultar_reconciliacion_eventos')
    def test_divergencia_no_genera_accion_automatica(self, consultar):
        consultar.return_value = self._respuesta_cloud('DIVERGENCIA_CONTENIDO')

        plan = Command()._construir_plan(90)

        self.assertEqual(plan['acciones'], [])
        self.assertEqual(plan['resumen']['DIVERGENCIA_CONTENIDO'], 1)

    @patch('apps.sync.engine.SyncEngine.consultar_reconciliacion_eventos')
    def test_plan_aprobado_revalida_reencola_y_deja_antes_despues(self, consultar):
        consultar.return_value = self._respuesta_cloud()
        plan = Command()._construir_plan(90)

        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', encoding='utf-8', delete=False,
        ) as archivo:
            json.dump(plan, archivo)
            ruta = Path(archivo.name)
        try:
            salida = io.StringIO()
            call_command(
                'reparar_bug_k',
                ejecutar=True,
                plan_aprobado=str(ruta),
                confirmar_plan=plan['plan_id'],
                stdout=salida,
            )

            self.evento.refresh_from_db()
            self.assertEqual(self.evento.estado, 'PENDIENTE')
            log = LogSync.objects.get(tipo='REPARACION')
            self.assertEqual(log.detalle['plan_id'], plan['plan_id'])
            self.assertEqual(log.detalle['eventos'], [{
                'event_id': str(self.evento.event_id),
                'hash_payload': self.evento.hash_payload,
                'antes': 'CONFIRMADO',
                'despues': 'PENDIENTE',
            }])

            with self.assertRaisesMessage(CommandError, 'ya fue aplicado'):
                call_command(
                    'reparar_bug_k',
                    ejecutar=True,
                    plan_aprobado=str(ruta),
                    confirmar_plan=plan['plan_id'],
                )
        finally:
            ruta.unlink(missing_ok=True)

    def test_ejecutar_exige_archivo_y_digest_aprobados(self):
        with self.assertRaisesMessage(CommandError, '--ejecutar exige'):
            call_command('reparar_bug_k', ejecutar=True)
