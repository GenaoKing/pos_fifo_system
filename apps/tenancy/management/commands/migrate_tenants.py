from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.tenancy.context import force_tenancy, tenant_context
from apps.tenancy.models import Tenant
from apps.tenancy.registry import configure_tenant_database


class Command(BaseCommand):
    help = 'Corre migraciones en una o todas las bases de datos tenant.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', help='tenant_key especifico.')
        parser.add_argument('--noinput', action='store_true', help='No pedir input interactivo.')

    def handle(self, *args, **options):
        qs = Tenant.objects.using('default').filter(activo=True).order_by('tenant_key')
        if options.get('tenant'):
            qs = qs.filter(tenant_key=options['tenant'])

        tenants = list(qs)
        if options.get('tenant') and not tenants:
            raise CommandError(f'Tenant "{options["tenant"]}" no existe o esta inactivo.')

        if not tenants:
            self.stdout.write(self.style.WARNING('No hay tenants activos para migrar.'))
            return

        with force_tenancy(True):
            for tenant in tenants:
                _, alias = configure_tenant_database(tenant)
                self.stdout.write(f'Migrando {tenant.tenant_key} ({alias})...')
                with tenant_context(tenant):
                    call_command(
                        'migrate',
                        database=alias,
                        interactive=not options.get('noinput'),
                        verbosity=options.get('verbosity', 1),
                    )
        self.stdout.write(self.style.SUCCESS(f'Tenants migrados: {len(tenants)}.'))
