"""Contrato del consumidor SUS-014 en ``bootstrap_tenant``."""

from contextlib import ExitStack, contextmanager, nullcontext
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import TestCase

from apps.suscripciones.seed import PlanDesconocido
from apps.tenancy.models import Tenant


class BootstrapTenantPlanSlugTests(TestCase):
    """El plan se verifica en el alias tenant antes de publicar cualquiera de sus proyecciones."""

    @contextmanager
    def _bootstrap_patched(self, *, validation_side_effect=None):
        from apps.tenancy.management.commands import bootstrap_tenant

        real_atomic = transaction.atomic

        def atomic_selectivo(*args, **kwargs):
            using = kwargs.get('using') or (args[0] if args else None)
            if str(using).startswith('tnt_'):
                return nullcontext()
            return real_atomic(*args, **kwargs)

        with ExitStack() as stack:
            stack.enter_context(patch.object(bootstrap_tenant.Command, '_ensure_database'))
            stack.enter_context(patch.object(bootstrap_tenant, 'configure_tenant_database'))
            stack.enter_context(patch.object(
                bootstrap_tenant,
                'tenant_context',
                side_effect=lambda tenant, **kwargs: nullcontext(tenant),
            ))
            stack.enter_context(patch.object(bootstrap_tenant, 'call_command'))
            seed_tenant = stack.enter_context(patch.object(
                bootstrap_tenant.Command,
                '_seed_tenant',
                return_value={
                    'token': 'sync-token',
                    'admin_username': 'admin',
                    'admin_password_applied': False,
                },
            ))
            stack.enter_context(patch.object(
                bootstrap_tenant.Command,
                '_seed_control_plane',
            ))
            stack.enter_context(patch.object(
                bootstrap_tenant.transaction,
                'atomic',
                side_effect=atomic_selectivo,
            ))
            validation_kwargs = {'return_value': None}
            if validation_side_effect is not None:
                validation_kwargs = {'side_effect': validation_side_effect}
            validar = stack.enter_context(patch.object(
                bootstrap_tenant,
                'validar_plan_slug',
                **validation_kwargs,
            ))
            yield validar, seed_tenant

    @staticmethod
    def _run_bootstrap(*, tenant, plan):
        call_command(
            'bootstrap_tenant',
            tenant=tenant,
            nombre=f'Tenant {tenant}',
            admin_email=f'admin@{tenant}.local',
            admin_password='A9!clave-larga-segura',
            identity_password='B8!portal-distinto-seguro',
            plan=plan,
            stdout=StringIO(),
        )

    def test_plan_valido_usa_alias_tenant_y_se_publica_despues_del_seed(self):
        with self._bootstrap_patched() as (validar, seed_tenant):
            self._run_bootstrap(tenant='plan_valido', plan='pro')

        validar.assert_called_once_with('pro', using='tnt_plan_valido')
        self.assertEqual(seed_tenant.call_args.kwargs['plan_slug'], 'pro')
        tenant = Tenant.objects.get(tenant_key='plan_valido')
        self.assertEqual(tenant.plan_slug, 'pro')
        self.assertEqual(tenant.estado_provisioning, Tenant.EstadoProvisioning.ACTIVE)

    def test_plan_vacio_es_valido_y_no_asigna_un_plan(self):
        with self._bootstrap_patched() as (validar, seed_tenant):
            self._run_bootstrap(tenant='plan_vacio', plan='')

        validar.assert_called_once_with('', using='tnt_plan_vacio')
        self.assertEqual(seed_tenant.call_args.kwargs['plan_slug'], '')
        self.assertEqual(
            Tenant.objects.get(tenant_key='plan_vacio').plan_slug,
            '',
        )

    def test_plan_inexistente_no_publica_slug_ni_siembra_suscripcion(self):
        with self._bootstrap_patched(
            validation_side_effect=PlanDesconocido('plan inexistente'),
        ) as (validar, seed_tenant):
            with self.assertRaisesMessage(CommandError, 'plan inexistente'):
                self._run_bootstrap(tenant='plan_invalido', plan='no-existe')

        validar.assert_called_once_with('no-existe', using='tnt_plan_invalido')
        # La suscripcion se crea exclusivamente desde _seed_tenant; no llegar
        # aqui demuestra que el slug invalido no escribe ninguna proyeccion.
        seed_tenant.assert_not_called()
        tenant = Tenant.objects.get(tenant_key='plan_invalido')
        self.assertEqual(tenant.plan_slug, '')
        self.assertEqual(tenant.estado_provisioning, Tenant.EstadoProvisioning.FAILED)

    def test_reintento_conserva_plan_previo_hasta_que_el_nuevo_valida(self):
        tenant = Tenant.objects.create(
            tenant_key='plan_reintento',
            slug='plan-reintento',
            nombre='Tenant anterior',
            plan_slug='basico',
            activo=False,
            estado_provisioning=Tenant.EstadoProvisioning.FAILED,
        )

        def validar_en_alias(slug, *, using):
            tenant.refresh_from_db()
            self.assertEqual(tenant.plan_slug, 'basico')
            self.assertEqual(slug, 'pro')
            self.assertEqual(using, 'tnt_plan_reintento')

        with self._bootstrap_patched(
            validation_side_effect=validar_en_alias,
        ) as (validar, seed_tenant):
            self._run_bootstrap(tenant='plan_reintento', plan='pro')

        validar.assert_called_once_with('pro', using='tnt_plan_reintento')
        self.assertEqual(seed_tenant.call_args.kwargs['plan_slug'], 'pro')
        tenant.refresh_from_db()
        self.assertEqual(tenant.plan_slug, 'pro')
        self.assertEqual(tenant.estado_provisioning, Tenant.EstadoProvisioning.ACTIVE)
        self.assertEqual(tenant.provisioning_intentos, 1)
