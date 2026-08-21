"""
Ejecuta otro management command con el tenant activo.

Antes solo aceptaba argumentos POSICIONALES: cualquier comando con una opcion
nombrada moria en argparse antes siquiera de validar el tenant
(`with_tenant --tenant X check --deploy` -> "unrecognized arguments: --deploy").
El wrapper documentado no servia para la mayoria de los comandos reales.

Ahora todo lo que va despues de `--` se reenvia tal cual.
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.tenancy.context import force_tenancy, tenant_context
from apps.tenancy.models import Tenant


class Command(BaseCommand):
    help = (
        'Ejecuta otro management command con tenant activo. '
        'Todo lo que sigue a `--` se pasa al comando destino: '
        'manage.py with_tenant --tenant demo -- check --deploy'
    )

    def add_arguments(self, parser):
        parser.add_argument('--tenant', required=True, help='tenant_key tecnico.')
        parser.add_argument('command', help='Comando Django a ejecutar.')
        parser.add_argument(
            'command_args',
            nargs='*',
            help='Argumentos del comando. Usa `--` antes de las opciones '
                 'nombradas para que argparse no las reclame como propias.',
        )

    def handle(self, *args, **options):
        tenant = Tenant.objects.using('default').filter(
            tenant_key=options['tenant'],
            activo=True,
        ).first()
        if tenant is None:
            raise CommandError(f'Tenant "{options["tenant"]}" no existe o esta inactivo.')

        argumentos = list(options['command_args'])
        # `parse_known_args` deja los desconocidos en `args`; con `--` argparse
        # los entrega ahi tambien. Se reenvian todos, en orden.
        argumentos.extend(args)

        # AVISO de contrato: activar el contexto NO vuelve tenant-aware las
        # transacciones del comando destino. Un `transaction.atomic()` sin
        # `using=` sigue abriendose sobre `default` aunque el router mande los
        # modelos a la base del tenant. Un comando que escriba en el tenant
        # dentro de un atomic implicito NO tiene rollback real.
        self.stdout.write(
            self.style.WARNING(
                f'Ejecutando "{options["command"]}" con tenant '
                f'{tenant.tenant_key} ({tenant.db_name}).'
            )
        )
        self.stdout.write(
            '  OJO: el contexto no cambia el alias de transaccion. Si el comando '
            'usa transaction.atomic() sin `using`, ese bloque corre sobre '
            '`default` y no protege las escrituras del tenant.'
        )

        with force_tenancy(True):
            with tenant_context(tenant):
                call_command(options['command'], *argumentos)
