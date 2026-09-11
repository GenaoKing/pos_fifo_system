"""Comprueba que retirar el bypass legacy ADMIN no deje operadores bloqueados."""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.permisos.engine import TODAS, asignaciones_efectivas
from apps.tenancy.management.base import TenantCommandMixin


class Command(TenantCommandMixin, BaseCommand):
    help = (
        'Preflight read-only: exige que cada ADMIN activo tenga una asignacion '
        'RBAC explicita con permisos.administrar.'
    )

    def add_arguments(self, parser):
        self.add_tenant_argument(parser, required=False)

    def handle(self, *args, **options):
        tenant_key = options.get('tenant')
        if tenant_key:
            tenant = self.get_tenant(tenant_key)
            return self.run_in_tenant(tenant, self._verificar)
        if getattr(settings, 'TENANCY_DB_PER_TENANT_ENABLED', False):
            raise CommandError('--tenant es obligatorio con tenancy habilitado.')
        return self._verificar()

    def _verificar(self):
        Usuario = get_user_model()
        admins = Usuario.objects.filter(rol='ADMIN', activo=True)
        cubiertos = set()

        negocios = set(
            admins.exclude(negocio_id__isnull=True)
            .values_list('negocio_id', flat=True)
        )
        for negocio_id in negocios:
            cubiertos.update(
                asignaciones_efectivas(
                    negocio_id=negocio_id,
                    sucursal=TODAS,
                ).filter(
                    usuario__rol='ADMIN',
                    rol__permisos__codigo='permisos.administrar',
                ).values_list('usuario_id', flat=True)
            )

        bloqueadores = list(
            admins.exclude(pk__in=cubiertos)
            .order_by('pk')
            .values_list('pk', flat=True)
        )
        self.stdout.write(f'admins_activos={admins.count()}')
        self.stdout.write(f'admins_con_rbac_explicito={len(cubiertos)}')
        self.stdout.write(f'bloqueadores={len(bloqueadores)}')
        if bloqueadores:
            self.stdout.write(f'ids_bloqueados={bloqueadores}')
            raise CommandError(
                'No desactivar RBAC_LEGACY_ADMIN_BYPASS: hay ADMIN activos '
                'sin asignacion explicita con permisos.administrar.'
            )

        self.stdout.write(self.style.SUCCESS(
            'Preflight OK: el bypass ADMIN puede desactivarse en este alcance.'
        ))
