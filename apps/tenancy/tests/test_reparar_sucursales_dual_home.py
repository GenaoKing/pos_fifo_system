"""Regresion del grafo cloud y del reparador dirigido de sucursales."""

import importlib
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from apps.tenancy.migration_repair import EstadoSucursales


def _estado(*, tabla_presente, migraciones=('0001_initial',)):
    return EstadoSucursales(
        alias='default',
        tabla_presente=tabla_presente,
        migraciones_registradas=migraciones,
    )


class GrafoSucursalesDualHomeTests(TestCase):
    def test_grafo_limpio_materializa_sucursal_antes_de_la_fk_de_auditoria(self):
        loader = MigrationLoader(connection, ignore_no_migrations=True)
        plan = loader.graph.forwards_plan(
            ('auditoria', '0002_auditoria_sucursal_and_more')
        )

        self.assertLess(
            plan.index(('sucursales', '0001_initial')),
            plan.index(('auditoria', '0002_auditoria_sucursal_and_more')),
        )

    def test_guardia_no_materializa_una_tabla_heredada(self):
        migracion = importlib.import_module(
            'apps.sucursales.migrations.0004_sucursal_dual_home_preflight'
        )
        apps = SimpleNamespace(
            get_model=lambda *args: SimpleNamespace(
                _meta=SimpleNamespace(db_table='sucursales_sucursal')
            )
        )
        schema_editor = SimpleNamespace(
            connection=SimpleNamespace(
                introspection=SimpleNamespace(table_names=lambda: [])
            )
        )

        with self.assertRaisesMessage(RuntimeError, 'No se materializa automaticamente'):
            migracion.exigir_tabla_sucursal(apps, schema_editor)

    def test_guardia_acepta_una_tabla_creada_por_migracion_limpia(self):
        migracion = importlib.import_module(
            'apps.sucursales.migrations.0004_sucursal_dual_home_preflight'
        )
        apps = SimpleNamespace(
            get_model=lambda *args: SimpleNamespace(
                _meta=SimpleNamespace(db_table='sucursales_sucursal')
            )
        )
        schema_editor = SimpleNamespace(
            connection=SimpleNamespace(
                introspection=SimpleNamespace(
                    table_names=lambda: ['sucursales_sucursal']
                )
            )
        )

        migracion.exigir_tabla_sucursal(apps, schema_editor)


class RepararSucursalesDualHomeCommandTests(TestCase):
    def test_dry_run_informa_las_filas_y_no_escribe(self):
        salida = StringIO()
        with (
            patch(
                'apps.tenancy.management.commands.reparar_sucursales_dual_home.'
                'inspeccionar_sucursales', return_value=_estado(tabla_presente=False),
            ),
            patch(
                'apps.tenancy.management.commands.reparar_sucursales_dual_home.'
                'desregistrar_migraciones_sucursales',
            ) as desregistrar,
            patch(
                'apps.tenancy.management.commands.reparar_sucursales_dual_home.'
                'call_command',
            ) as migrate,
        ):
            call_command(
                'reparar_sucursales_dual_home', database='default', stdout=salida,
            )

        self.assertIn('LEDGER', salida.getvalue())
        self.assertIn('0001_initial', salida.getvalue())
        self.assertIn('DRY-RUN', salida.getvalue())
        desregistrar.assert_not_called()
        migrate.assert_not_called()

    def test_apply_repara_unicamente_su_historial_y_reaplica_la_app(self):
        salida = StringIO()
        pendiente = _estado(tabla_presente=False, migraciones=('0001_initial', '0002_x'))
        reparada = _estado(
            tabla_presente=True,
            migraciones=(
                '0001_initial', '0002_sucursal_ultima_sync_sucursal_usuario_servicio',
                '0003_sucursal_negocio', '0004_sucursal_dual_home_preflight',
            ),
        )
        with (
            patch(
                'apps.tenancy.management.commands.reparar_sucursales_dual_home.'
                'inspeccionar_sucursales', side_effect=(pendiente, reparada),
            ),
            patch(
                'apps.tenancy.management.commands.reparar_sucursales_dual_home.'
                'desregistrar_migraciones_sucursales', return_value=2,
            ) as desregistrar,
            patch(
                'apps.tenancy.management.commands.reparar_sucursales_dual_home.'
                'call_command',
            ) as migrate,
        ):
            call_command(
                'reparar_sucursales_dual_home', database='default', apply=True,
                verbosity=1, stdout=salida,
            )

        desregistrar.assert_called_once_with('default')
        migrate.assert_called_once_with(
            'migrate', 'sucursales', database='default', interactive=False,
            verbosity=1,
        )
        self.assertIn('REPARADA', salida.getvalue())

    def test_no_intenta_reparar_una_base_limpia(self):
        salida = StringIO()
        with patch(
            'apps.tenancy.management.commands.reparar_sucursales_dual_home.'
            'inspeccionar_sucursales', return_value=_estado(tabla_presente=True),
        ):
            call_command(
                'reparar_sucursales_dual_home', database='default', stdout=salida,
            )

        self.assertIn('Sin reparacion', salida.getvalue())

    def test_apply_y_dry_run_son_excluyentes(self):
        with self.assertRaisesMessage(CommandError, 'Use --apply o --dry-run'):
            call_command(
                'reparar_sucursales_dual_home', database='default', apply=True,
                dry_run=True,
            )


class MigrateCloudPreflightTests(TestCase):
    def test_migrate_cloud_frena_y_remite_al_reparador(self):
        with (
            patch(
                'apps.tenancy.management.commands.migrate_cloud.inspeccionar_sucursales',
                return_value=_estado(tabla_presente=False),
            ),
            patch('apps.tenancy.management.commands.migrate_cloud.call_command') as migrate,
            self.assertRaisesMessage(CommandError, 'No se reparo automaticamente'),
        ):
            call_command('migrate_cloud', noinput=True)

        migrate.assert_not_called()
