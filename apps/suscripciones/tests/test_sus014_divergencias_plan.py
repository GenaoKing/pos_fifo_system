"""
apps/suscripciones/tests/test_sus014_divergencias_plan.py

SUS-014 (segunda mitad) — `validar_plan_slug` (ya cableada por Codex en
`bootstrap_tenant`) impide que un aprovisionamiento NUEVO deje el control
plane y la BD tenant inconsistentes, pero no detecta un plan que ya divergio
por otra via: una fila tocada a mano, un bootstrap corrido antes del fix,
etc. `divergencias_plan_operativo` es ese chequeo de postcondicion, de solo
lectura, pensado para sumarse a `divergencias_identidad`
(`apps/tenancy/services.py`) el mismo lugar que ya usa
`manage.py verificar_identidad_tenant` -- cablearlo ahi es pedido a Codex
(ver handoff SUS014-divergencias-plan.md).
"""
from django.test import TestCase
from django.utils.text import slugify

from apps.negocios.models import Negocio
from apps.suscripciones import engine, seed
from apps.suscripciones.models import Modulo, Plan, SuscripcionNegocio


def _negocio(nombre='Royal Plast'):
    return Negocio.objects.create(nombre=nombre, slug=slugify(nombre))


class DivergenciasPlanOperativoTests(TestCase):
    def setUp(self):
        # Los tres planes default ya vienen de la migracion 0002_seed_suscripciones;
        # idempotente por si el estado historico cambia.
        seed.sembrar_modulos(Modulo)
        seed.crear_planes_default(Plan, Modulo)
        self.negocio = _negocio()

    def _suscribir(self, *, plan_slug=None, activa=True):
        plan = Plan.objects.get(slug=plan_slug) if plan_slug else None
        return SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=plan, activa=activa,
        )

    def test_negocio_none_no_reporta_nada(self):
        """`divergencias_identidad` ya reporta NEGOCIO_MISSING; no duplicar."""
        self.assertEqual(
            engine.divergencias_plan_operativo('empresarial', None), [],
        )

    def test_sin_plan_en_ningun_lado_no_diverge(self):
        """Control plane vacio + suscripcion sin fila: 'sin plan' en ambos lados."""
        self.assertEqual(
            engine.divergencias_plan_operativo('', self.negocio), [],
        )

    def test_plan_coincide_no_diverge(self):
        self._suscribir(plan_slug='pro')
        self.assertEqual(
            engine.divergencias_plan_operativo('pro', self.negocio), [],
        )

    def test_plan_distinto_diverge(self):
        self._suscribir(plan_slug='pro')
        diferencias = engine.divergencias_plan_operativo('empresarial', self.negocio)
        self.assertEqual(diferencias, [{
            'code': 'PLAN_DRIFT',
            'field': 'plan_slug',
            'expected': 'empresarial',
            'actual': 'pro',
        }])

    def test_control_plane_anuncia_plan_pero_operativo_es_custom(self):
        """Suscripcion existe pero `plan=None` (custom): el control plane miente."""
        self._suscribir(plan_slug=None)
        diferencias = engine.divergencias_plan_operativo('empresarial', self.negocio)
        self.assertEqual(diferencias, [{
            'code': 'PLAN_DRIFT',
            'field': 'plan_slug',
            'expected': 'empresarial',
            'actual': '',
        }])

    def test_control_plane_vacio_pero_operativo_tiene_plan(self):
        """El typo tambien puede irse en el otro sentido: nadie lo anuncio y ya esta activo."""
        self._suscribir(plan_slug='basico')
        diferencias = engine.divergencias_plan_operativo('', self.negocio)
        self.assertEqual(diferencias, [{
            'code': 'PLAN_DRIFT',
            'field': 'plan_slug',
            'expected': '',
            'actual': 'basico',
        }])

    def test_negocio_sin_suscripcion_pero_control_plane_anuncia_plan(self):
        """Ni siquiera existe la fila de SuscripcionNegocio: sigue siendo divergencia."""
        self.assertFalse(SuscripcionNegocio.objects.filter(negocio=self.negocio).exists())
        diferencias = engine.divergencias_plan_operativo('pro', self.negocio)
        self.assertEqual(diferencias, [{
            'code': 'PLAN_DRIFT',
            'field': 'plan_slug',
            'expected': 'pro',
            'actual': '',
        }])

    def test_suscripcion_suspendida_igual_compara_su_plan(self):
        """`activa=False` no cambia la comparacion: el plan operativo sigue siendo el suyo."""
        self._suscribir(plan_slug='pro', activa=False)
        self.assertEqual(
            engine.divergencias_plan_operativo('pro', self.negocio), [],
        )
