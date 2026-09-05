from django.core.management.base import BaseCommand, CommandError

from apps.tenancy.management.base import TenantCommandMixin

from apps.notificaciones.models import SuscripcionPush


class Command(TenantCommandMixin, BaseCommand):
    help = 'Desactiva dispositivos push de un tenant sin borrar su historial.'

    def add_arguments(self, parser):
        self.add_tenant_argument(parser)
        parser.add_argument('--usuario', help='Username concreto.')
        parser.add_argument(
            '--todos-dispositivos', action='store_true',
            help='Confirmacion explicita para desactivar todos los dispositivos.',
        )

    def handle(self, *args, **options):
        if not options.get('usuario') and not options['todos_dispositivos']:
            raise CommandError('Indica --usuario o --todos-dispositivos.')
        tenant = self.get_tenant(options['tenant'])

        def desactivar():
            qs = SuscripcionPush.objects.filter(activa=True)
            if options.get('usuario'):
                qs = qs.filter(usuario__username=options['usuario'])
            total = qs.update(activa=False)
            self.stdout.write(self.style.SUCCESS(
                f'{tenant.tenant_key}: {total} dispositivo(s) desactivado(s).'
            ))

        self.run_in_tenant(tenant, desactivar)
