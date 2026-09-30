"""Exercise clientes.0006 against populated PostgreSQL historical tables."""
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase
from django.utils import timezone


class ClienteContadoHistoricalMigrationTests(TransactionTestCase):
    before = ('clientes', '0005_cliente_origen_cloud_id')
    target = ('clientes', '0006_cliente_contado_singleton')

    def setUp(self):
        super().setUp()
        if connection.vendor != 'postgresql':
            self.skipTest('Pending deferred FK trigger events are a PostgreSQL regression.')
        if not str(connection.settings_dict['NAME']).startswith('test_'):
            raise AssertionError('Historical DDL tests require a runner-owned test database.')
        executor = MigrationExecutor(connection)
        self.final_targets = executor.loader.graph.leaf_nodes()
        executor.migrate([self.before])
        executor = MigrationExecutor(connection)
        historical = executor.loader.project_state(list(executor.loader.applied_migrations)).apps
        self.Cliente = historical.get_model('clientes', 'Cliente')
        self.Venta = historical.get_model('ventas', 'Venta')
        self.Cuenta = historical.get_model('cuentas_por_cobrar', 'CuentaPorCobrar')
        self.Cotizacion = historical.get_model('cotizaciones', 'Cotizacion')
        Usuario = historical.get_model('usuarios', 'Usuario')
        Metodo = historical.get_model('cuentas_por_cobrar', 'MetodoPlazoCredito')
        usuario = Usuario.objects.create(username='contado_migration', password='!', rol='ADMIN')
        metodo = Metodo.objects.create(nombre='Plazo prueba migracion', dias_vencimiento=30)
        self.survivor = self.Cliente.objects.create(nombre='CLIENTE CONTADO', tipo='CONTADO')
        self.duplicate = self.Cliente.objects.create(nombre=' cliente contado ', tipo='CONTADO')
        self.venta = self.Venta.objects.create(
            numero_venta='MIG-CONTADO-001', fecha_venta=timezone.now(), usuario=usuario,
            cliente=self.duplicate, subtotal=Decimal('100'), total=Decimal('100'),
        )
        self.cuenta = self.Cuenta.objects.create(
            cliente=self.duplicate, venta=self.venta, metodo_plazo=metodo,
            total=Decimal('100'), saldo_original=Decimal('100'), saldo=Decimal('100'),
            fecha_limite=timezone.localdate(), creado_por=usuario,
        )
        self.cotizacion = self.Cotizacion.objects.create(
            numero_cotizacion='MIG-COT-001', cliente=self.duplicate, usuario=usuario,
            fecha_creacion=timezone.now(), subtotal=Decimal('100'), total=Decimal('100'),
        )

    def tearDown(self):
        try:
            if hasattr(self, 'final_targets'):
                # A rejected real-person fixture must not prevent restoration
                # of the test schema for the next test.
                for model_name in ('Cuenta', 'Cotizacion', 'Venta', 'Cliente'):
                    model = getattr(self, model_name, None)
                    if model is not None:
                        model.objects.all().delete()
                MigrationExecutor(connection).migrate(self.final_targets)
        finally:
            super().tearDown()

    def test_duplicate_with_deferred_financial_fks_consolidates_before_unique_index(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT condeferrable, condeferred FROM pg_constraint "
                "WHERE contype = 'f' AND confrelid = %s::regclass "
                "AND conrelid IN (%s::regclass, %s::regclass, %s::regclass)",
                [self.Cliente._meta.db_table, self.Venta._meta.db_table,
                 self.Cuenta._meta.db_table, self.Cotizacion._meta.db_table],
            )
            foreign_keys = cursor.fetchall()
        self.assertEqual(len(foreign_keys), 3)
        self.assertTrue(all(deferrable and deferred for deferrable, deferred in foreign_keys))

        MigrationExecutor(connection).migrate([self.target])

        self.assertEqual(list(self.Cliente.objects.filter(tipo='CONTADO').values_list('pk', flat=True)),
                         [self.survivor.pk])
        for instance in (self.venta, self.cuenta, self.cotizacion):
            instance.refresh_from_db()
            self.assertEqual(instance.cliente_id, self.survivor.pk)
            self.assertEqual(instance.total, Decimal('100'))
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, self.Cliente._meta.db_table)
        self.assertTrue(constraints['cliente_contado_singleton']['unique'])
        self.assertIn(self.target, MigrationRecorder(connection).applied_migrations())

    def test_real_person_aborts_without_reassigning_financial_history(self):
        real = self.Cliente.objects.create(nombre='Persona con identidad', tipo='CONTADO', cedula_rnc='00123456789')
        ids_before = list(self.Cliente.objects.order_by('id').values_list('id', flat=True))

        with self.assertRaisesMessage(RuntimeError, 'historia comercial'):
            MigrationExecutor(connection).migrate([self.target])

        self.assertEqual(list(self.Cliente.objects.order_by('id').values_list('id', flat=True)), ids_before)
        self.assertTrue(self.Cliente.objects.filter(pk=real.pk, tipo='CONTADO').exists())
        for instance in (self.venta, self.cuenta, self.cotizacion):
            instance.refresh_from_db()
            self.assertEqual(instance.cliente_id, self.duplicate.pk)
        self.assertNotIn(self.target, MigrationRecorder(connection).applied_migrations())

    def test_index_failure_rolls_back_the_consolidation_in_the_same_transaction(self):
        ids_before = list(self.Cliente.objects.order_by('id').values_list('id', flat=True))
        with patch('django.db.backends.postgresql.schema.DatabaseSchemaEditor.add_constraint',
                   side_effect=RuntimeError('forced index failure')):
            with self.assertRaisesMessage(RuntimeError, 'forced index failure'):
                MigrationExecutor(connection).migrate([self.target])
        self.assertEqual(list(self.Cliente.objects.order_by('id').values_list('id', flat=True)), ids_before)
        for instance in (self.venta, self.cuenta, self.cotizacion):
            instance.refresh_from_db()
            self.assertEqual(instance.cliente_id, self.duplicate.pk)
        self.assertNotIn(self.target, MigrationRecorder(connection).applied_migrations())
