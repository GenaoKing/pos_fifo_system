"""Physical PostgreSQL regression of the observed legacy control-plane shape."""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase

from apps.tenancy.migration_repair import (
    AUDITORIA_CONTROL_HISTORIAL_LEGACY,
    TABLA_AUDITORIA,
    inspeccionar_auditoria_control,
    materializar_auditoria_control_fantasma,
)


class RepararAuditoriaControlTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        if connection.vendor != 'postgresql':
            self.skipTest('La reparacion operativa dirigida usa PostgreSQL.')
        if not str(connection.settings_dict['NAME']).startswith('test_'):
            raise AssertionError('El ensayo de DDL solo admite una BD creada por el test runner.')
        executor = MigrationExecutor(connection)
        self.final_targets = executor.loader.graph.leaf_nodes()
        legacy = [('auditoria', AUDITORIA_CONTROL_HISTORIAL_LEGACY[-1])]
        executor.migrate(legacy)
        self.historical = executor.loader.project_state(legacy).apps.get_model('auditoria', 'Auditoria')
        # Only a disposable test DB is altered. Preserve the recorded 0001..4,
        # reproducing the router's skipped historical operations exactly.
        with connection.schema_editor() as editor:
            editor.delete_model(self.historical)

    def tearDown(self):
        try:
            if hasattr(self, 'final_targets'):
                if TABLA_AUDITORIA not in connection.introspection.table_names():
                    materializar_auditoria_control_fantasma('default', AUDITORIA_CONTROL_HISTORIAL_LEGACY)
                MigrationExecutor(connection).migrate(self.final_targets)
        finally:
            super().tearDown()

    def history(self):
        return list(MigrationRecorder(connection).migration_qs.order_by('id')
                    .values_list('id', 'app', 'name', 'applied'))

    def test_default_dry_run_preserves_schema_and_complete_migration_history(self):
        before = self.history()
        tables = set(connection.introspection.table_names())
        output = StringIO()
        call_command('reparar_auditoria_dual_home', database='default', stdout=output)
        self.assertIn('DRY-RUN', output.getvalue())
        self.assertIn('0004_alter_auditoria_accion', output.getvalue())
        self.assertIn('sucursales_sucursal', output.getvalue())
        self.assertEqual(self.history(), before)
        self.assertEqual(set(connection.introspection.table_names()), tables)

    def test_apply_creates_historical_table_indexes_and_foreign_keys_then_migrate_succeeds(self):
        before = self.history()
        call_command('reparar_auditoria_dual_home', database='default', apply=True, stdout=StringIO())
        self.assertEqual(self.history(), before)
        with connection.cursor() as cursor:
            columns = {field.name for field in connection.introspection.get_table_description(cursor, TABLA_AUDITORIA)}
            constraints = connection.introspection.get_constraints(cursor, TABLA_AUDITORIA)
        self.assertEqual(columns, {field.column for field in self.historical._meta.local_fields})
        self.assertNotIn('event_id', columns)
        self.assertNotIn('actor_nombre', columns)
        for index in self.historical._meta.indexes:
            self.assertIn(index.name, constraints)
        references = {item['foreign_key'][0] for item in constraints.values() if item.get('foreign_key')}
        self.assertEqual(references, {'usuarios', 'django_content_type', 'sucursales_sucursal'})

        row = self.historical.objects.using('default').create(accion='CONFIG', descripcion='historical row preserved')
        call_command('reparar_auditoria_dual_home', database='default', apply=True, stdout=StringIO())
        self.assertEqual(self.historical.objects.using('default').get(pk=row.pk).descripcion, 'historical row preserved')
        self.assertEqual(self.history(), before)

        call_command('migrate', 'auditoria', database='default', interactive=False, verbosity=0, stdout=StringIO())
        from apps.auditoria.models import Auditoria
        current = Auditoria.objects.using('default').get(pk=row.pk)
        self.assertIsNotNone(current.event_id)
        self.assertEqual(current.descripcion, 'historical row preserved')

    def test_cloud_preflight_fails_before_any_migrate_call(self):
        before = self.history()
        with patch('apps.tenancy.management.commands.migrate_cloud.call_command') as migrate:
            with self.assertRaisesMessage(CommandError, 'reparar_auditoria_dual_home --database default --dry-run'):
                call_command('migrate_cloud', noinput=True, skip_tenants=True, stdout=StringIO())
            migrate.assert_not_called()
        self.assertEqual(self.history(), before)
        self.assertFalse(inspeccionar_auditoria_control().tabla_presente)

    def test_later_history_is_rejected_without_creating_a_table(self):
        recorder = MigrationRecorder(connection)
        unexpected = '0005_auditoria_inmutable_y_actor'
        recorder.record_applied('auditoria', unexpected)
        try:
            before = self.history()
            with self.assertRaisesMessage(CommandError, 'historial de auditoria no reconocido'):
                call_command('reparar_auditoria_dual_home', database='default', apply=True, stdout=StringIO())
            self.assertEqual(self.history(), before)
            self.assertNotIn(TABLA_AUDITORIA, connection.introspection.table_names())
        finally:
            recorder.record_unapplied('auditoria', unexpected)

    def test_missing_dependency_blocks_even_dry_run(self):
        tables = [table for table in connection.introspection.table_names() if table != 'sucursales_sucursal']
        before = self.history()
        with patch.object(connection.introspection, 'table_names', return_value=tables):
            with self.assertRaisesMessage(CommandError, 'reparar_sucursales_dual_home'):
                call_command('reparar_auditoria_dual_home', database='default', stdout=StringIO())
        self.assertEqual(self.history(), before)
        self.assertNotIn(TABLA_AUDITORIA, connection.introspection.table_names())

    def test_postcondition_failure_rolls_back_table_creation(self):
        state = inspeccionar_auditoria_control()
        before = self.history()
        with patch('apps.tenancy.migration_repair.inspeccionar_auditoria_control', return_value=state):
            with self.assertRaisesMessage(CommandError, 'postcondicion'):
                materializar_auditoria_control_fantasma('default', state.migraciones_registradas)
        self.assertEqual(self.history(), before)
        self.assertNotIn(TABLA_AUDITORIA, connection.introspection.table_names())

    def test_tenant_alias_and_stale_preflight_are_rejected(self):
        before = self.history()
        with self.assertRaisesMessage(CommandError, '--database default'):
            materializar_auditoria_control_fantasma('tnt_real_customer', AUDITORIA_CONTROL_HISTORIAL_LEGACY)
        with self.assertRaisesMessage(CommandError, 'historial de auditoria cambio'):
            materializar_auditoria_control_fantasma('default', ('0001_initial',))
        self.assertEqual(self.history(), before)
        self.assertNotIn(TABLA_AUDITORIA, connection.introspection.table_names())
