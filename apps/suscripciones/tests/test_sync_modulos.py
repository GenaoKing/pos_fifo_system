"""
SUS-017 — `sync_modulos` prometia sincronizar los planes default y no lo
hacia: `crear_planes_default` solo asignaba modulos al crear el plan (o si
estaba vacio), asi que editar `seed.TIERS` y re-correr el comando no movia
nada. `sincronizar_planes_preset` es la version que SI resincroniza un plan
gestionado (`preset_version` no nulo) cuando su tier cambio de version, y
deja en paz un plan personalizado (`preset_version=None`).
"""
from unittest import mock

from django.test import TestCase

from apps.suscripciones import seed
from apps.suscripciones.models import Modulo, Plan


class SincronizarPlanesPresetTests(TestCase):
    def setUp(self):
        seed.sembrar_modulos(Modulo)
        # La migracion 0002 ya siembra basico/pro/empresarial (y 0003 los deja
        # en preset_version=1): se limpia para probar el camino "creado" desde
        # una tabla vacia, como en una instalacion recien migrada a este campo
        # que perdio sus planes por otra via.
        Plan.objects.all().delete()

    def _tiers_v2_basico_sin_barcode(self):
        """TIERS con 'basico' en v2: le quita `barcode_scanner`."""
        tiers = dict(seed.TIERS)
        nombre, descripcion, _keys, _version = tiers['basico']
        tiers['basico'] = (nombre, descripcion, ['impresion_termica'], 2)
        return tiers

    def test_crea_los_tres_planes_con_la_version_vigente(self):
        resultados = []
        seed.sincronizar_planes_preset(
            Plan, Modulo, reportar=lambda slug, accion: resultados.append((slug, accion)),
        )

        self.assertEqual(
            {slug for slug, accion in resultados if accion == 'creado'},
            {'basico', 'pro', 'empresarial'},
        )
        basico = Plan.objects.get(slug='basico')
        self.assertEqual(basico.preset_version, 1)
        self.assertEqual(
            set(basico.modulos.values_list('key', flat=True)),
            {'impresion_termica', 'barcode_scanner'},
        )

    def test_version_vigente_no_hace_nada(self):
        seed.sincronizar_planes_preset(Plan, Modulo)  # crea en v1

        resultados = []
        seed.sincronizar_planes_preset(
            Plan, Modulo, reportar=lambda slug, accion: resultados.append((slug, accion)),
        )

        self.assertEqual({accion for _slug, accion in resultados}, {'sin_cambios'})

    def test_subir_la_version_del_tier_resincroniza_los_modulos(self):
        """
        La reproduccion exacta de SUS-017: se retira un modulo del tier
        (aca `barcode_scanner` de 'basico', subiendo a v2) y re-correr el
        sync SI lo refleja -- antes no pasaba nada.
        """
        seed.sincronizar_planes_preset(Plan, Modulo)  # crea 'basico' en v1
        basico = Plan.objects.get(slug='basico')
        self.assertIn('barcode_scanner', basico.modulos.values_list('key', flat=True))

        resultados = []
        with mock.patch.object(seed, 'TIERS', self._tiers_v2_basico_sin_barcode()):
            seed.sincronizar_planes_preset(
                Plan, Modulo, reportar=lambda slug, accion: resultados.append((slug, accion)),
            )

        basico.refresh_from_db()
        self.assertEqual(basico.preset_version, 2)
        self.assertEqual(
            set(basico.modulos.values_list('key', flat=True)),
            {'impresion_termica'},
        )
        self.assertIn(('basico', 'actualizado'), resultados)
        # pro/empresarial no cambiaron de version: sin tocar.
        self.assertIn(('pro', 'sin_cambios'), resultados)

    def test_plan_personalizado_no_se_toca(self):
        seed.sincronizar_planes_preset(Plan, Modulo)  # crea 'basico' en v1
        basico = Plan.objects.get(slug='basico')
        basico.preset_version = None  # desenganchado a proposito (Admin)
        basico.modulos.set(Modulo.objects.filter(key='ecf'))  # edicion manual
        basico.save(update_fields=['preset_version'])

        resultados = []
        with mock.patch.object(seed, 'TIERS', self._tiers_v2_basico_sin_barcode()):
            seed.sincronizar_planes_preset(
                Plan, Modulo, reportar=lambda slug, accion: resultados.append((slug, accion)),
            )

        basico.refresh_from_db()
        self.assertIsNone(basico.preset_version)
        self.assertEqual(set(basico.modulos.values_list('key', flat=True)), {'ecf'})
        self.assertIn(('basico', 'personalizado'), resultados)

    def test_comando_reporta_y_no_falla(self):
        from io import StringIO

        from django.core.management import call_command

        salida = StringIO()
        call_command('sync_modulos', stdout=salida)

        texto = salida.getvalue()
        self.assertIn('Plan basico: creado', texto)
        self.assertIn('Planes: 3', texto)


class BackfillPresetVersionMigracionTests(TestCase):
    """La migracion que introdujo el campo (0003) solo marca `preset_version`
    en el plan cuyo set de modulos ACTUAL coincide exacto con la v1 congelada;
    el resto queda `None` (personalizado), sin pisar un drift real."""

    def _correr_backfill(self):
        import importlib

        from django.apps import apps as apps_reales

        modulo = importlib.import_module(
            'apps.suscripciones.migrations.0003_plan_preset_version',
        )
        schema_editor_falso = mock.Mock(connection=mock.Mock(alias='default'))
        modulo.backfill_preset_version(apps_reales, schema_editor_falso)

    def test_plan_fresco_con_la_composicion_v1_exacta_se_marca(self):
        seed.sembrar_modulos(Modulo)
        seed.crear_planes_default(Plan, Modulo)  # deja basico/pro/empresarial en v1
        Plan.objects.update(preset_version=None)  # simula "antes del campo"

        self._correr_backfill()

        self.assertEqual(Plan.objects.get(slug='basico').preset_version, 1)
        self.assertEqual(Plan.objects.get(slug='empresarial').preset_version, 1)

    def test_plan_ya_divergido_no_se_marca(self):
        seed.sembrar_modulos(Modulo)
        seed.crear_planes_default(Plan, Modulo)
        basico = Plan.objects.get(slug='basico')
        basico.modulos.add(Modulo.objects.get(key='ecf'))  # ya diverge de v1
        Plan.objects.update(preset_version=None)

        self._correr_backfill()

        self.assertIsNone(Plan.objects.get(slug='basico').preset_version)
