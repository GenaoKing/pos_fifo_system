"""SUS-016: checkpoint read-only de bootstrap y sync por instalación."""
from io import StringIO
import json

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.configuracion.models import ConfiguracionNegocio
from apps.negocios.models import Negocio
from apps.sucursales.models import Sucursal
from apps.suscripciones import seed
from apps.suscripciones.checkpoint import construir_checkpoint
from apps.suscripciones.models import Modulo, NegocioModulo, Plan, SuscripcionNegocio
from apps.sync.models import DiferidoSync, LogSync, VersionMaestro


@override_settings(SUCURSAL_CODIGO='SUS-016')
class CheckpointSuscripcionesSyncTests(TestCase):
    def setUp(self):
        self.negocio = Negocio.objects.create(nombre='Negocio checkpoint', slug='checkpoint')
        self.sucursal = Sucursal.objects.create(
            codigo='SUS-016', nombre='Sucursal checkpoint', activa=True,
            negocio=self.negocio,
        )
        ConfiguracionNegocio.objects.create(
            sucursal=self.sucursal, nombre_negocio='Checkpoint',
        )
        seed.bootstrap(
            ModuloModel=Modulo,
            PlanModel=Plan,
            NegocioModel=Negocio,
            NegocioModuloModel=NegocioModulo,
            SuscripcionModel=SuscripcionNegocio,
            ConfiguracionModel=ConfiguracionNegocio,
        )

    def _snapshot_db(self):
        return {
            'modulos': list(Modulo.objects.order_by('pk').values('id', 'key', 'core')),
            'planes': list(Plan.objects.order_by('pk').values('id', 'slug', 'preset_version')),
            'suscripciones': list(
                SuscripcionNegocio.objects.order_by('pk').values('id', 'negocio_id', 'plan_id', 'activa')
            ),
            'overrides': list(NegocioModulo.objects.order_by('pk').values('id', 'negocio_id', 'modulo_id', 'incluido')),
            'logs': LogSync.objects.count(),
            'cursores': VersionMaestro.objects.count(),
            'diferidos': DiferidoSync.objects.count(),
        }

    def test_checkpoint_listo_es_determinista_y_no_muta(self):
        antes = self._snapshot_db()

        primero = construir_checkpoint()
        segundo = construir_checkpoint()

        self.assertEqual(primero['estado'], 'LISTO')
        self.assertEqual(primero['checkpoint'], segundo['checkpoint'])
        self.assertEqual(primero['modo'], 'SOLO_LECTURA')
        self.assertEqual(self._snapshot_db(), antes)

    def test_detecta_bootstrap_parcial_y_sync_pendiente_sin_repararlos(self):
        SuscripcionNegocio.objects.filter(negocio=self.negocio).delete()
        VersionMaestro.objects.create(
            tabla='configuracion', bloqueado_desde=timezone.now(),
            bloqueado_detalle='payload invalido',
        )
        DiferidoSync.objects.create(
            tenant_key='checkpoint', sucursal_codigo='SUS-016', tabla='roles',
            identidad='slug=supervisor', payload_hash='a' * 64, payload={},
        )
        LogSync.objects.create(tipo='FULL', resultado='PARCIAL', sucursal=self.sucursal)
        antes = self._snapshot_db()

        reporte = construir_checkpoint()

        self.assertEqual(reporte['estado'], 'PARCIAL')
        self.assertEqual(
            {problema['codigo'] for problema in reporte['problemas']},
            {
                'NEGOCIO_SIN_SUSCRIPCION',
                'SYNC_CURSOR_BLOQUEADO',
                'SYNC_DIFERIDOS_PENDIENTES',
                'SYNC_ULTIMO_CICLO_PARCIAL',
            },
        )
        self.assertEqual(self._snapshot_db(), antes)

    def test_json_y_strict_exponen_el_mismo_checkpoint_sin_mutar(self):
        SuscripcionNegocio.objects.filter(negocio=self.negocio).delete()
        antes = self._snapshot_db()
        salida = StringIO()

        call_command('verificar_suscripciones_sync', '--json', stdout=salida)

        reporte = json.loads(salida.getvalue())
        self.assertEqual(reporte['estado'], 'PARCIAL')
        self.assertEqual(self._snapshot_db(), antes)

        with self.assertRaisesMessage(CommandError, 'Checkpoint parcial'):
            call_command('verificar_suscripciones_sync', '--strict', stdout=StringIO())
        self.assertEqual(self._snapshot_db(), antes)

    def test_preset_personalizado_se_reporta_sin_tratarlo_como_drift(self):
        plan = Plan.objects.get(slug='basico')
        plan.preset_version = None
        plan.save(update_fields=['preset_version'])

        reporte = construir_checkpoint()

        basico = next(plan for plan in reporte['planes_preset'] if plan['slug'] == 'basico')
        self.assertEqual(basico['estado'], 'PERSONALIZADO')
        self.assertEqual(reporte['estado'], 'LISTO')
