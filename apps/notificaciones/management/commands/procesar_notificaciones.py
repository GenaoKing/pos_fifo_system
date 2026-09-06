from django.core.management.base import BaseCommand, CommandError

from apps.tenancy.context import force_tenancy, tenant_context
from apps.tenancy.models import Tenant
from apps.tenancy.registry import configure_tenant_database

from apps.notificaciones.services import ejecutar_ciclo


class Command(BaseCommand):
    help = 'Proyecta eventos sync y envia Web Push, aislando cada tenant.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', help='Procesa solo este tenant activo.')
        parser.add_argument('--limite-eventos', type=int, default=200)
        parser.add_argument('--limite-push', type=int, default=500)

    def handle(self, *args, **options):
        qs = Tenant.objects.using('default').filter(activo=True).order_by('tenant_key')
        if options.get('tenant'):
            qs = qs.filter(tenant_key=options['tenant'])
        tenants = list(qs)
        if options.get('tenant') and not tenants:
            raise CommandError(f'Tenant "{options["tenant"]}" no existe o esta inactivo.')

        errores = []
        with force_tenancy(True):
            for tenant in tenants:
                try:
                    configure_tenant_database(tenant)
                    with tenant_context(tenant):
                        resultado = ejecutar_ciclo(
                            limite_eventos=max(1, options['limite_eventos']),
                            limite_push=max(1, options['limite_push']),
                        )
                except Exception as exc:
                    errores.append(tenant.tenant_key)
                    self.stderr.write(self.style.ERROR(
                        f'{tenant.tenant_key}: {type(exc).__name__}'
                    ))
                    continue
                resumen = ', '.join(f'{k}={v}' for k, v in resultado.items())
                self.stdout.write(self.style.SUCCESS(f'{tenant.tenant_key}: {resumen}'))
        if errores:
            raise CommandError(f'Fallaron {len(errores)} tenant(s): {", ".join(errores)}')
