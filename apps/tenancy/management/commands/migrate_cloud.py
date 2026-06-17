from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Corre migraciones cloud: control plane y luego bases tenant activas.'

    def add_arguments(self, parser):
        parser.add_argument('--noinput', action='store_true', help='No pedir input interactivo.')
        parser.add_argument('--skip-tenants', action='store_true', help='Solo migrar control plane.')

    def handle(self, *args, **options):
        verbosity = options.get('verbosity', 1)
        interactive = not options.get('noinput')

        self.stdout.write('Migrando control plane...')
        call_command('migrate', interactive=interactive, verbosity=verbosity)

        if options.get('skip_tenants'):
            self.stdout.write(self.style.WARNING('Migracion de tenants omitida.'))
            return

        self.stdout.write('Migrando tenants activos...')
        call_command('migrate_tenants', noinput=options.get('noinput'), verbosity=verbosity)
        self.stdout.write(self.style.SUCCESS('Migraciones cloud completadas.'))
