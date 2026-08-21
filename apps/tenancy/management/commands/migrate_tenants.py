from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.tenancy.context import force_tenancy, tenant_context
from apps.tenancy.models import Tenant
from apps.tenancy.registry import configure_tenant_database


class Command(BaseCommand):
    help = 'Corre migraciones en una o todas las bases de datos tenant.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', help='tenant_key especifico.')
        parser.add_argument('--noinput', action='store_true', help='No pedir input interactivo.')
        parser.add_argument(
            '--incluir-inactivos',
            action='store_true',
            help='Incluye tenants inactivos. Lo usa el bootstrap: un tenant en '
                 'aprovisionamiento esta inactivo a proposito hasta que su base '
                 'quede lista, y aun asi hay que migrarla.',
        )
        parser.add_argument(
            '--continuar-ante-fallo',
            action='store_true',
            help='Sigue con el resto de la flota si un tenant falla. Sin esto '
                 'se corta en el primero, que es el default seguro para no '
                 'esparcir una migracion rota.',
        )

    def handle(self, *args, **options):
        qs = Tenant.objects.using('default').order_by('tenant_key')
        if not options.get('incluir_inactivos'):
            qs = qs.filter(activo=True)
        if options.get('tenant'):
            qs = qs.filter(tenant_key=options['tenant'])

        tenants = list(qs)
        if options.get('tenant') and not tenants:
            raise CommandError(f'Tenant "{options["tenant"]}" no existe o esta inactivo.')

        if not tenants:
            self.stdout.write(self.style.WARNING('No hay tenants activos para migrar.'))
            return

        # LEDGER POR TENANT. Antes el bucle se detenia en la primera excepcion y
        # solo imprimia el total si TODOS terminaban: si fallaba el tercero de
        # cinco, el job moria sin decir cuales quedaron migrados y cuales no.
        # El estado real dependia de leer el log completo.
        resultados = []
        fallo = None

        with force_tenancy(True):
            for tenant in tenants:
                inicio = timezone.now()
                try:
                    _, alias = configure_tenant_database(
                        tenant, permitir_inactivo=options.get('incluir_inactivos', False),
                    )
                    self.stdout.write(f'Migrando {tenant.tenant_key} ({alias})...')
                    with tenant_context(tenant):
                        call_command(
                            'migrate',
                            database=alias,
                            interactive=not options.get('noinput'),
                            verbosity=options.get('verbosity', 1),
                        )
                except Exception as exc:
                    resultados.append({
                        'tenant': tenant.tenant_key,
                        'estado': 'FALLO',
                        'segundos': (timezone.now() - inicio).total_seconds(),
                        'error': f'{type(exc).__name__}: {exc}',
                    })
                    fallo = exc
                    if not options.get('continuar_ante_fallo'):
                        break
                else:
                    resultados.append({
                        'tenant': tenant.tenant_key,
                        'estado': 'OK',
                        'segundos': (timezone.now() - inicio).total_seconds(),
                        'error': '',
                    })

        self._imprimir_ledger(resultados, total=len(tenants))

        if fallo is not None:
            pendientes = [
                t.tenant_key for t in tenants
                if t.tenant_key not in {r['tenant'] for r in resultados}
            ]
            raise CommandError(
                f'Migracion de flota incompleta. '
                f'Migrados: {sum(1 for r in resultados if r["estado"] == "OK")}/'
                f'{len(tenants)}. '
                f'Sin intentar: {pendientes or "ninguno"}. '
                f'Re-ejecutar migra solo lo que falte (migrate es idempotente).'
            )

    def _imprimir_ledger(self, resultados, total):
        """Resumen por tenant, se haya completado la flota o no."""
        self.stdout.write('')
        self.stdout.write('Resultado por tenant:')
        for fila in resultados:
            estilo = self.style.SUCCESS if fila['estado'] == 'OK' else self.style.ERROR
            linea = f'  {fila["estado"]:<6} {fila["tenant"]:<24} {fila["segundos"]:.1f}s'
            if fila['error']:
                linea += f'  {fila["error"][:200]}'
            self.stdout.write(estilo(linea))

        ok = sum(1 for r in resultados if r['estado'] == 'OK')
        self.stdout.write('')
        if ok == total:
            self.stdout.write(self.style.SUCCESS(f'Tenants migrados: {ok}/{total}.'))
        else:
            self.stdout.write(self.style.ERROR(f'Tenants migrados: {ok}/{total}.'))
