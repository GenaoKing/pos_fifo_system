from django.core.management.base import BaseCommand

from apps.tenancy.management.base import TenantCommandMixin


class Command(TenantCommandMixin, BaseCommand):
    help = 'Valida el tenant y muestra el comando pg_dump recomendado.'

    def add_arguments(self, parser):
        self.add_tenant_argument(parser)
        parser.add_argument('--output', help='Ruta destino sugerida para el dump.')

    def handle(self, *args, **options):
        tenant = self.get_tenant(options['tenant'])
        output = options.get('output') or f'{tenant.db_name}.dump'
        self.stdout.write(self.style.WARNING(
            'Backup real no se ejecuta en Fase 1 desde Django; usar pg_dump operativo.'
        ))
        self.stdout.write(
            f'pg_dump --format=custom --file="{output}" "{tenant.db_name}"'
        )
