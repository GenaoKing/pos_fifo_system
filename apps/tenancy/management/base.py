from django.core.management.base import CommandError

from apps.tenancy.context import force_tenancy, tenant_context
from apps.tenancy.models import Tenant


class TenantCommandMixin:
    def add_tenant_argument(self, parser, required=True):
        parser.add_argument(
            '--tenant',
            required=required,
            help='tenant_key tecnico. Ej: demo, royalplast.',
        )

    def get_tenant(self, tenant_key):
        tenant = Tenant.objects.using('default').filter(
            tenant_key=tenant_key,
            activo=True,
        ).first()
        if tenant is None:
            raise CommandError(f'Tenant "{tenant_key}" no existe o esta inactivo.')
        return tenant

    def run_in_tenant(self, tenant, callback):
        with force_tenancy(True):
            with tenant_context(tenant):
                return callback()
