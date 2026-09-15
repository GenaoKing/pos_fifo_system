"""
apps/suscripciones/tests/test_sus014_validar_plan_slug.py

SUS-014 — `bootstrap_tenant --plan <slug>` (Codex) escribia el slug en
`Tenant.plan_slug` (control plane) sin validarlo contra la base del tenant;
si el Plan no existia ahi, la asignacion en la base tenant se omitia en
silencio y el control plane quedaba anunciando un plan que la suscripcion
operativa nunca tuvo.

Este modulo cubre la pieza que corresponde a C03: la validacion en si.
Cablearla ANTES de escribir ambas bases en `bootstrap_tenant` es pedido
explicito a Codex (ver handoff). Los tres planes default (basico/pro/
empresarial) ya existen tras `migrate` (`0002_seed_suscripciones`); estos
tests los reutilizan en vez de recrearlos para no chocar con el `unique` de
`slug`.
"""
from django.test import TestCase

from apps.suscripciones.models import Plan
from apps.suscripciones.seed import PlanDesconocido, validar_plan_slug


class ValidarPlanSlugTests(TestCase):
    def test_slug_vacio_es_valido(self):
        """Vacio == 'sin plan asignado explicitamente'; no hay nada que validar."""
        validar_plan_slug('')
        validar_plan_slug(None)

    def test_slug_existente_no_levanta(self):
        self.assertTrue(Plan.objects.filter(slug='pro').exists())

        validar_plan_slug('pro')

    def test_slug_desconocido_levanta(self):
        with self.assertRaises(PlanDesconocido) as ctx:
            validar_plan_slug('plan-que-no-existe')

        self.assertIn('plan-que-no-existe', str(ctx.exception))

    def test_el_mensaje_lista_los_planes_disponibles(self):
        with self.assertRaises(PlanDesconocido) as ctx:
            validar_plan_slug('empresarial-typo')

        mensaje = str(ctx.exception)
        self.assertIn('basico', mensaje)
        self.assertIn('pro', mensaje)
        self.assertIn('empresarial', mensaje)

    def test_acepta_using_explicito(self):
        """
        DB-per-tenant: `bootstrap_tenant` debe pasar el alias de la base del
        TENANT, no dejar que se mire `default` por omision. El aislamiento
        real entre bases fisicas lo cubre la matriz TEN-016 (dos BDs
        PostgreSQL); aca solo se confirma que el parametro se respeta.
        """
        validar_plan_slug('pro', using='default')

        with self.assertRaises(PlanDesconocido):
            validar_plan_slug('plan-que-no-existe', using='default')
