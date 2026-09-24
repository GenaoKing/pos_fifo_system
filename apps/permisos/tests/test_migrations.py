"""Regresiones de migraciones históricas de permisos."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class CT02IdentidadMigracionTests(TransactionTestCase):
    """`permisos.0011` debe migrar roles y asignaciones ya existentes."""

    migrate_from = [('permisos', '0010_notificaciones_administrar')]
    migrate_to = [('permisos', '0011_ct02_identidad_revisiones')]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.executor = MigrationExecutor(connection)
        cls.executor.migrate(cls.migrate_from)
        cls.old_apps = cls.executor.loader.project_state(cls.migrate_from).apps

    @classmethod
    def tearDownClass(cls):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDownClass()

    def test_backfill_genera_un_uuid_por_fila_y_conserva_unicidad(self):
        Negocio = self.old_apps.get_model('negocios', 'Negocio')
        Usuario = self.old_apps.get_model('usuarios', 'Usuario')
        Rol = self.old_apps.get_model('permisos', 'Rol')
        AsignacionRol = self.old_apps.get_model('permisos', 'AsignacionRol')

        negocio = Negocio.objects.create(
            nombre='Negocio migracion CT-02',
            slug='negocio-migracion-ct02',
        )
        administrador = Rol.objects.create(
            negocio=negocio,
            nombre='Administrador',
            slug='administrador',
            es_sistema=True,
        )
        cajero = Rol.objects.create(
            negocio=negocio,
            nombre='Cajero',
            slug='cajero',
            es_sistema=True,
        )
        usuarios = [
            Usuario.objects.create(
                username=f'usuario_migracion_{indice}',
                email=f'usuario_migracion_{indice}@test.local',
                password='!',
            )
            for indice in range(2)
        ]
        for usuario in usuarios:
            AsignacionRol.objects.create(usuario=usuario, rol=administrador)
            AsignacionRol.objects.create(usuario=usuario, rol=cajero)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        migrated_apps = executor.loader.project_state(self.migrate_to).apps
        RolMigrado = migrated_apps.get_model('permisos', 'Rol')
        AsignacionMigrada = migrated_apps.get_model('permisos', 'AsignacionRol')

        role_ids = list(
            RolMigrado.objects.filter(negocio_id=negocio.pk)
            .order_by('pk')
            .values_list('cloud_id', flat=True)
        )
        assignment_ids = list(
            AsignacionMigrada.objects.filter(rol__negocio_id=negocio.pk)
            .order_by('pk')
            .values_list('cloud_id', flat=True)
        )

        self.assertEqual(len(role_ids), 2)
        self.assertNotIn(None, role_ids)
        self.assertEqual(len(set(role_ids)), len(role_ids))
        self.assertEqual(len(assignment_ids), 4)
        self.assertNotIn(None, assignment_ids)
        self.assertEqual(len(set(assignment_ids)), len(assignment_ids))
