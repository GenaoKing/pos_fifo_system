"""The branch token command must register cloud authentication end to end."""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from rest_framework.authtoken.models import Token

from apps.negocios.models import Negocio
from apps.sucursales.models import Sucursal
from apps.tenancy.context import force_tenancy, reset_current_tenant, set_current_tenant
from apps.tenancy.models import SyncToken, Tenant


class VincularSucursalTokenTests(TestCase):
    def setUp(self):
        negocio = Negocio.objects.create(
            nombre='POS FIFO Staging Demo', slug='pos-fifo-staging-demo',
        )
        self.sucursal = Sucursal.objects.create(
            codigo='QA-PC-01', nombre='PC de QA', negocio=negocio,
        )

    def test_cloud_registers_control_hash_and_rotates_both_tokens(self):
        tenant = Tenant.objects.create(
            tenant_key='staging_demo', slug='pos-fifo-staging-demo',
            nombre='POS FIFO Staging Demo',
        )
        tokens = set_current_tenant(tenant.tenant_key, 'default')
        try:
            with force_tenancy(True):
                output = StringIO()
                call_command(
                    'vincular_sucursal_token', sucursal=self.sucursal.codigo,
                    stdout=output,
                )
                self.sucursal.refresh_from_db()
                user = get_user_model().objects.get(
                    pk=self.sucursal.usuario_servicio_id,
                )
                self.assertFalse(user.has_usable_password())
                old_token = Token.objects.get(user=user).key
                registry = SyncToken.objects.get(
                    tenant=tenant, sucursal_codigo=self.sucursal.codigo,
                )
                self.assertTrue(registry.activo)
                self.assertEqual(
                    registry.token_hash, SyncToken.hash_token(old_token),
                )

                call_command(
                    'vincular_sucursal_token', sucursal=self.sucursal.codigo,
                    stdout=StringIO(),
                )
                self.assertEqual(Token.objects.get(user=user).key, old_token)
                self.assertEqual(SyncToken.objects.count(), 1)

                call_command(
                    'vincular_sucursal_token', sucursal=self.sucursal.codigo,
                    regenerar=True, stdout=StringIO(),
                )
                new_token = Token.objects.get(user=user).key
                registry.refresh_from_db()
                self.assertNotEqual(new_token, old_token)
                self.assertEqual(
                    registry.token_hash, SyncToken.hash_token(new_token),
                )
        finally:
            reset_current_tenant(tokens)

    def test_local_command_does_not_require_control_plane(self):
        call_command(
            'vincular_sucursal_token', sucursal=self.sucursal.codigo,
            stdout=StringIO(),
        )
        self.assertEqual(SyncToken.objects.count(), 0)
        self.assertEqual(Token.objects.count(), 1)

    def test_conflicting_control_hash_requires_explicit_rotation(self):
        tenant = Tenant.objects.create(
            tenant_key='staging_demo', slug='pos-fifo-staging-demo',
            nombre='POS FIFO Staging Demo',
        )
        SyncToken.objects.create(
            tenant=tenant, sucursal_codigo=self.sucursal.codigo,
            token_hash=SyncToken.hash_token('old-token'),
        )
        tokens = set_current_tenant(tenant.tenant_key, 'default')
        try:
            with force_tenancy(True):
                with self.assertRaisesMessage(CommandError, '--regenerar'):
                    call_command(
                        'vincular_sucursal_token', sucursal=self.sucursal.codigo,
                        stdout=StringIO(),
                    )
                self.assertEqual(
                    SyncToken.objects.get(tenant=tenant).token_hash,
                    SyncToken.hash_token('old-token'),
                )
        finally:
            reset_current_tenant(tokens)
