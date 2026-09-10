from io import StringIO
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase

from apps.auditoria.models import Auditoria
from apps.tenancy.models import Identity, Membership, SyncToken, Tenant
from apps.tenancy.services import (
    divergencias_identidad,
    marcar_estado_provisioning,
    preparar_tenant_provisioning,
)


class TenancyModelTests(TestCase):
    def test_tenant_defaults_are_derived_from_tenant_key(self):
        tenant = Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        self.assertEqual(tenant.db_name, 'tnt_demo')
        self.assertEqual(tenant.media_prefix, 'demo/')

    def test_tenant_slug_is_unique_when_derived_from_name(self):
        first = Tenant.objects.create(tenant_key='demo', nombre='Mi Empresa')
        second = Tenant.objects.create(tenant_key='demo2', nombre='Mi Empresa')

        self.assertEqual(first.slug, 'mi-empresa')
        self.assertNotEqual(second.slug, first.slug)
        self.assertTrue(second.slug.startswith('mi-empresa'))

    def test_identidad_de_routing_es_inmutable(self):
        tenant = Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        tenant.slug = 'otro'

        with self.assertRaisesMessage(ValidationError, 'routing inmutable'):
            tenant.save(update_fields=['slug'])

    def test_rnc_se_canoniza_y_colisiona_antes_de_provisionar(self):
        first = Tenant.objects.create(
            tenant_key='demo', slug='demo', nombre='Demo', rnc='1-01-12345-6',
        )
        self.assertEqual(first.rnc_canonico, '101123456')

        with self.assertRaises(ValidationError):
            Tenant.objects.create(
                tenant_key='demo2', slug='demo2', nombre='Demo 2', rnc='101123456',
            )

    def test_sync_token_hash_is_stable_and_does_not_store_plain_token(self):
        token = 'secret-token'
        digest = SyncToken.hash_token(token)
        self.assertEqual(digest, SyncToken.hash_token(token))
        self.assertNotIn(token, digest)
        self.assertEqual(len(digest), 64)

    def test_verificador_detecta_drift_sin_corregir_proyecciones(self):
        tenant = Tenant.objects.create(
            tenant_key='demo', slug='demo', nombre='Autoridad', rnc='101123456',
        )
        negocio = SimpleNamespace(
            slug='demo', nombre='Nombre viejo', rnc_canonico='101123456', activo=True,
        )
        config = SimpleNamespace(
            nombre_negocio='Autoridad', rnc='999999999',
            sucursal=SimpleNamespace(codigo='SD-001'),
        )

        diferencias = divergencias_identidad(tenant, negocio, [config])

        self.assertEqual(
            {(fila['code'], fila['field']) for fila in diferencias},
            {('NEGOCIO_DRIFT', 'nombre'), ('CONFIG_DRIFT', 'rnc')},
        )
        self.assertEqual(negocio.nombre, 'Nombre viejo')
        self.assertEqual(config.rnc, '999999999')


class BootstrapTenantDryRunTests(TestCase):
    def test_dry_run_does_not_create_control_plane_rows(self):
        out = StringIO()
        call_command(
            'bootstrap_tenant',
            tenant='demo',
            nombre='Demo Tenant',
            admin_email='admin@demo.local',
            dry_run=True,
            stdout=out,
        )
        self.assertIn('DRY-RUN', out.getvalue())
        self.assertEqual(Tenant.objects.count(), 0)

    def test_rechaza_reutilizar_el_mismo_secreto_en_local_y_portal(self):
        with self.assertRaisesMessage(CommandError, 'deben ser distintas'):
            call_command(
                'bootstrap_tenant',
                tenant='demo',
                nombre='Demo Tenant',
                admin_email='admin@demo.local',
                admin_password='A9!clave-larga-segura',
                identity_password='A9!clave-larga-segura',
                dry_run=True,
            )

        self.assertEqual(Tenant.objects.count(), 0)


class BootstrapTenantLifecycleTests(TestCase):
    def test_preparacion_y_auditoria_comparten_transaccion(self):
        with patch(
            'apps.tenancy.services.registrar_mutacion',
            side_effect=RuntimeError('audit sink'),
        ), self.assertRaises(RuntimeError):
            preparar_tenant_provisioning(
                tenant_key='sin_huerfano',
                slug='sin-huerfano',
                nombre='Sin huerfano',
            )

        self.assertFalse(Tenant.objects.filter(tenant_key='sin_huerfano').exists())

    def test_checkpoint_y_auditoria_comparten_transaccion(self):
        tenant = Tenant.objects.create(
            tenant_key='estado', slug='estado', nombre='Estado', activo=False,
        )

        with self.assertRaises(RuntimeError):
            with transaction.atomic(using='default'):
                marcar_estado_provisioning(
                    tenant,
                    Tenant.EstadoProvisioning.DB_READY,
                    incrementar_intento=True,
                )
                raise RuntimeError('rollback esperado')

        tenant.refresh_from_db()
        self.assertEqual(tenant.estado_provisioning, Tenant.EstadoProvisioning.ACTIVE)
        self.assertEqual(tenant.provisioning_intentos, 0)
        self.assertFalse(
            Auditoria.objects.filter(
                tenant_key='estado', accion='tenant.provisioning.db_ready',
            ).exists()
        )

    def test_fallo_queda_reanudable_y_rerun_activa(self):
        from apps.tenancy.management.commands import bootstrap_tenant

        real_atomic = transaction.atomic

        def atomic_selectivo(*args, **kwargs):
            using = kwargs.get('using') or (args[0] if args else None)
            if str(using).startswith('tnt_'):
                return nullcontext()
            return real_atomic(*args, **kwargs)

        base_patches = (
            patch.object(bootstrap_tenant.Command, '_ensure_database'),
            patch.object(bootstrap_tenant, 'configure_tenant_database'),
            patch.object(
                bootstrap_tenant,
                'tenant_context',
                side_effect=lambda tenant, **kwargs: nullcontext(tenant),
            ),
            patch.object(bootstrap_tenant, 'call_command'),
            patch.object(
                bootstrap_tenant.Command,
                '_seed_tenant',
                return_value={
                    'token': 'sync-token',
                    'admin_username': 'admin',
                    'admin_password_applied': False,
                },
            ),
            patch.object(bootstrap_tenant.transaction, 'atomic', side_effect=atomic_selectivo),
        )
        with (
            base_patches[0], base_patches[1], base_patches[2],
            base_patches[3], base_patches[4], base_patches[5],
        ):
            with patch.object(
                bootstrap_tenant.Command,
                '_seed_control_plane',
                side_effect=RuntimeError('token=secreto-no-loguear'),
            ):
                with self.assertRaises(RuntimeError):
                    call_command(
                        'bootstrap_tenant',
                        tenant='retry',
                        nombre='Retry',
                        admin_email='admin@retry.local',
                        admin_password='A9!clave-larga-segura',
                        identity_password='B8!portal-distinto-seguro',
                    )

        tenant = Tenant.objects.get(tenant_key='retry')
        self.assertEqual(tenant.estado_provisioning, Tenant.EstadoProvisioning.FAILED)
        self.assertFalse(tenant.activo)
        self.assertEqual(tenant.provisioning_intentos, 1)
        self.assertNotIn('secreto-no-loguear', tenant.provisioning_error)

        base_patches = (
            patch.object(bootstrap_tenant.Command, '_ensure_database'),
            patch.object(bootstrap_tenant, 'configure_tenant_database'),
            patch.object(
                bootstrap_tenant,
                'tenant_context',
                side_effect=lambda tenant, **kwargs: nullcontext(tenant),
            ),
            patch.object(bootstrap_tenant, 'call_command'),
            patch.object(
                bootstrap_tenant.Command,
                '_seed_tenant',
                return_value={
                    'token': 'sync-token',
                    'admin_username': 'admin',
                    'admin_password_applied': False,
                },
            ),
            patch.object(bootstrap_tenant.transaction, 'atomic', side_effect=atomic_selectivo),
            patch.object(bootstrap_tenant.Command, '_seed_control_plane'),
        )
        with (
            base_patches[0], base_patches[1], base_patches[2],
            base_patches[3], base_patches[4], base_patches[5],
            base_patches[6],
        ):
            call_command(
                'bootstrap_tenant',
                tenant='retry',
                nombre='Retry',
                admin_email='admin@retry.local',
                admin_password='A9!clave-larga-segura',
                identity_password='B8!portal-distinto-seguro',
            )

        tenant.refresh_from_db()
        self.assertEqual(tenant.estado_provisioning, Tenant.EstadoProvisioning.ACTIVE)
        self.assertTrue(tenant.activo)
        self.assertEqual(tenant.provisioning_intentos, 2)
        self.assertEqual(tenant.provisioning_error, '')
        self.assertTrue(
            Auditoria.objects.filter(
                tenant_key='retry', accion='tenant.provisioning.failed',
                resultado=Auditoria.Resultado.FAILED,
            ).exists()
        )
    def test_explicit_duplicate_slug_fails_fast(self):
        Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')

        with self.assertRaisesMessage(CommandError, 'ya pertenece'):
            call_command(
                'bootstrap_tenant',
                tenant='demo2',
                nombre='Demo 2',
                slug='demo',
                dry_run=True,
            )

    def test_reusing_admin_email_in_other_tenant_fails_fast(self):
        tenant = Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        other = Tenant.objects.create(tenant_key='demo2', slug='demo2', nombre='Demo 2')
        identity = Identity.objects.create(email='admin@example.com')
        Membership.objects.create(identity=identity, tenant=tenant, username='admin')

        with self.assertRaisesMessage(CommandError, 'ya tiene una membresia activa'):
            call_command(
                'bootstrap_tenant',
                tenant=other.tenant_key,
                nombre='Demo 2',
                admin_email='admin@example.com',
                dry_run=True,
            )

    def test_reusing_admin_email_in_same_tenant_is_allowed(self):
        tenant = Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        identity = Identity.objects.create(email='admin@example.com')
        Membership.objects.create(identity=identity, tenant=tenant, username='admin')

        out = StringIO()
        call_command(
            'bootstrap_tenant',
            tenant='demo',
            nombre='Demo',
            admin_email='admin@example.com',
            dry_run=True,
            stdout=out,
        )

        self.assertIn('DRY-RUN', out.getvalue())


class NormalizeImportTenantCommandTests(TestCase):
    @staticmethod
    @contextmanager
    def _fake_tenant_context(tenant):
        yield tenant

    def _summary(self, token='sync-token-plain'):
        return {
            'negocio_id': 1,
            'sucursales': 1,
            'usuarios_sin_negocio': 0,
            'ventas_sin_sucursal': 0,
            'compras_sin_sucursal': 0,
            'lotes_sin_sucursal': 0,
            'admin_username': 'admin',
            'sync_token': token,
        }

    def _call_command(self, **overrides):
        options = {
            'tenant': 'demo',
            'nombre': 'Demo',
            'slug': 'demo',
            'sucursal_codigo': '01',
            'sucursal_nombre': 'Principal',
            'admin_email': 'admin@example.com',
            'admin_password': 'Admin123!',
            'identity_password': 'Portal456!Segura',
            'dry_run': True,
        }
        options.update(overrides)
        out = options.pop('stdout', StringIO())
        with patch(
            'apps.tenancy.management.commands.normalizar_import_tenant.tenant_context',
            self._fake_tenant_context,
        ):
            call_command('normalizar_import_tenant', stdout=out, **options)
        return out

    @patch('apps.tenancy.management.commands.normalizar_import_tenant.Command._normalize_tenant_db')
    def test_slug_distinto_falla_antes_de_tocar_tenant_db(self, normalize):
        Tenant.objects.create(tenant_key='demo', slug='estable', nombre='Demo')

        with self.assertRaisesMessage(CommandError, 'slug de routing es inmutable'):
            self._call_command(slug='otro')

        normalize.assert_not_called()

    @patch('apps.tenancy.management.commands.normalizar_import_tenant.Command._normalize_tenant_db')
    def test_normalizador_rechaza_un_secreto_compartido(self, normalize):
        Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')

        with self.assertRaisesMessage(CommandError, 'deben ser distintas'):
            self._call_command(
                admin_password='A9!clave-larga-segura',
                identity_password='A9!clave-larga-segura',
            )

        normalize.assert_not_called()

    @patch('apps.tenancy.management.commands.normalizar_import_tenant.Command._normalize_tenant_db')
    def test_reusing_admin_email_in_other_tenant_fails_before_tenant_db(self, normalize):
        tenant = Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        other = Tenant.objects.create(tenant_key='demo2', slug='demo2', nombre='Demo 2')
        identity = Identity.objects.create(email='admin@example.com')
        Membership.objects.create(identity=identity, tenant=other, username='admin')

        with self.assertRaisesMessage(CommandError, 'ya tiene una membresia activa'):
            self._call_command(tenant=tenant.tenant_key, admin_email=' Admin@Example.com ')

        normalize.assert_not_called()

    @patch('apps.tenancy.management.commands.normalizar_import_tenant.Command._normalize_tenant_db')
    def test_reusing_admin_email_in_same_tenant_is_allowed(self, normalize):
        tenant = Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        identity = Identity.objects.create(email='admin@example.com')
        Membership.objects.create(identity=identity, tenant=tenant, username='admin')
        normalize.return_value = self._summary()

        out = self._call_command(admin_email=' Admin@Example.com ')

        normalize.assert_called_once()
        self.assertIn('DRY-RUN', out.getvalue())

    @patch('apps.tenancy.management.commands.normalizar_import_tenant.Command._normalize_tenant_db')
    def test_sync_token_is_masked_by_default(self, normalize):
        Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        normalize.return_value = self._summary(token='plain-sync-token')

        out = self._call_command()

        self.assertIn('sync_token: plain-sy...', out.getvalue())
        self.assertNotIn('plain-sync-token', out.getvalue())

    @patch('apps.tenancy.management.commands.normalizar_import_tenant.Command._normalize_tenant_db')
    def test_show_sync_token_prints_plain_token_when_requested(self, normalize):
        Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        normalize.return_value = self._summary(token='plain-sync-token')

        out = self._call_command(show_sync_token=True)

        self.assertIn('sync_token: plain-sync-token', out.getvalue())
