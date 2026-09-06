from django.core.management.base import BaseCommand

from apps.tenancy.management.base import TenantCommandMixin

from apps.notificaciones import push
from apps.notificaciones.models import (
    EntregaPush,
    EventoSyncNotificacionProcesado,
    MotorNotificaciones,
    SuscripcionPush,
)


class Command(TenantCommandMixin, BaseCommand):
    help = 'Diagnostica configuracion y cola de notificaciones de un tenant.'

    def add_arguments(self, parser):
        self.add_tenant_argument(parser)

    def handle(self, *args, **options):
        tenant = self.get_tenant(options['tenant'])

        def verificar():
            motor = MotorNotificaciones.actual()
            pendientes = EntregaPush.objects.filter(
                estado=EntregaPush.PENDIENTE, suscripcion__activa=True,
            ).count()
            activas = SuscripcionPush.objects.filter(activa=True).count()
            en_reintento = EventoSyncNotificacionProcesado.objects.filter(
                estado=EventoSyncNotificacionProcesado.REINTENTO,
            ).count()
            fallidas = EventoSyncNotificacionProcesado.objects.filter(
                estado=EventoSyncNotificacionProcesado.FALLIDO,
            ).count()
            self.stdout.write(f'motor_activo={motor.activo}')
            self.stdout.write(f'activado_desde={motor.activado_desde or "no"}')
            self.stdout.write(f'vapid_configurado={push.configurado()}')
            self.stdout.write(f'suscripciones_activas={activas}')
            self.stdout.write(f'entregas_pendientes={pendientes}')
            self.stdout.write(f'proyecciones_en_reintento={en_reintento}')
            self.stdout.write(f'proyecciones_fallidas={fallidas}')

        self.run_in_tenant(tenant, verificar)
