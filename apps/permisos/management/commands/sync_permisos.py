"""
manage.py sync_permisos
Upsert del catalogo de permisos (apps/permisos/catalogo.py) en la tabla Permiso.

Idempotente. Correr tras agregar nuevos permisos al catalogo.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.permisos.catalogo import sembrar_catalogo
from apps.permisos.models import Permiso, Rol
from apps.tenancy.context import get_current_tenant_alias
from apps.tenancy.management.base import TenantCommandMixin


class Command(TenantCommandMixin, BaseCommand):
    help = 'Sincroniza el catalogo de permisos en la base de datos.'

    def add_arguments(self, parser):
        self.add_tenant_argument(parser, required=False)
        parser.add_argument(
            '--aplicar-presets-sistema',
            action='store_true',
            help=(
                'Actualiza explicitamente el rol Administrador con todo el '
                'catalogo. Sin este flag no se pisan roles existentes.'
            ),
        )

    def handle(self, *args, **options):
        tenant_key = options.get('tenant')
        if tenant_key:
            tenant = self.get_tenant(tenant_key)
            return self.run_in_tenant(
                tenant,
                lambda: self._sincronizar(options['aplicar_presets_sistema']),
            )
        if getattr(settings, 'TENANCY_DB_PER_TENANT_ENABLED', False):
            raise CommandError('--tenant es obligatorio con tenancy habilitado.')
        return self._sincronizar(options['aplicar_presets_sistema'])

    def _sincronizar(self, aplicar_presets):
        alias = get_current_tenant_alias() or 'default'
        with transaction.atomic(using=alias):
            creados, actualizados = sembrar_catalogo(Permiso, using=alias)

            admins_actualizados = 0
            if aplicar_presets:
                todos = list(Permiso.objects.using(alias).all())
                admins = Rol.objects.using(alias).filter(
                    es_sistema=True, slug='administrador',
                )
                for rol in admins:
                    rol.permisos.set(todos)
                    admins_actualizados += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Catalogo sincronizado: {creados} creados, {actualizados} actualizados '
                f'({Permiso.objects.count()} permisos en total). '
                f'Roles Administrador actualizados: {admins_actualizados}.'
            )
        )
