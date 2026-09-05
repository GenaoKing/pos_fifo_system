from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.tenancy.context import force_tenancy, tenant_context
from apps.tenancy.models import Tenant
from apps.tenancy.registry import configure_tenant_database

from apps.notificaciones.models import MotorNotificaciones


class Command(BaseCommand):
    help = 'Activa el motor desde ahora; nunca proyecta eventos historicos.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', help='tenant_key especifico.')
        parser.add_argument('--todos-los-tenants', action='store_true')
        parser.add_argument('--desactivar', action='store_true')

    def handle(self, *args, **options):
        if bool(options.get('tenant')) == bool(options.get('todos_los_tenants')):
            raise CommandError('Indica --tenant o --todos-los-tenants, exactamente uno.')
        qs = Tenant.objects.using('default').filter(activo=True).order_by('tenant_key')
        if options.get('tenant'):
            qs = qs.filter(tenant_key=options['tenant'])
        tenants = list(qs)
        if not tenants:
            raise CommandError('No se encontraron tenants activos.')

        with force_tenancy(True):
            for tenant in tenants:
                configure_tenant_database(tenant)
                with tenant_context(tenant):
                    motor = MotorNotificaciones.actual()
                    motor.activo = not options['desactivar']
                    if motor.activo:
                        motor.activado_desde = timezone.now()
                    motor.save(update_fields=['activo', 'activado_desde', 'actualizado_en'])
                estado = 'ACTIVO desde ahora' if not options['desactivar'] else 'DESACTIVADO'
                self.stdout.write(self.style.SUCCESS(f'{tenant.tenant_key}: {estado}'))
