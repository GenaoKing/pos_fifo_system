from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.tenancy.context import force_tenancy, tenant_context
from apps.tenancy.models import Tenant


class Command(BaseCommand):
    help = 'Ejecuta otro management command con tenant activo.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', required=True, help='tenant_key tecnico.')
        parser.add_argument('command', help='Comando Django a ejecutar.')
        parser.add_argument('command_args', nargs='*', help='Argumentos posicionales del comando.')

    def handle(self, *args, **options):
        tenant = Tenant.objects.using('default').filter(
            tenant_key=options['tenant'],
            activo=True,
        ).first()
        if tenant is None:
            raise CommandError(f'Tenant "{options["tenant"]}" no existe o esta inactivo.')

        with force_tenancy(True):
            with tenant_context(tenant):
                call_command(options['command'], *options['command_args'])
