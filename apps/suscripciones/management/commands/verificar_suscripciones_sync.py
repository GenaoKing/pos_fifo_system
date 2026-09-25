"""Checkpoint read-only de SUS-016 por instalación."""
import json

from django.core.management.base import BaseCommand, CommandError

from apps.suscripciones.checkpoint import construir_checkpoint


class Command(BaseCommand):
    help = (
        'Reporta el checkpoint de suscripciones/sync de esta instalación. '
        'Solo lectura: no ejecuta bootstrap, sync_modulos ni reparaciones.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', help='Emite JSON estable para archivar o comparar.')
        parser.add_argument(
            '--strict', action='store_true',
            help='Termina distinto de cero si el checkpoint queda PARCIAL.',
        )

    def handle(self, *args, **options):
        reporte = construir_checkpoint()
        if options['json']:
            self.stdout.write(json.dumps(reporte, indent=2, ensure_ascii=False))
        else:
            self._imprimir(reporte)

        if options['strict'] and reporte['estado'] != 'LISTO':
            raise CommandError('Checkpoint parcial: revisar el reporte antes de cualquier mutación.')

    def _imprimir(self, reporte):
        w = self.stdout.write
        estilo = self.style.ERROR if reporte['estado'] == 'PARCIAL' else self.style.SUCCESS
        instalacion = reporte['instalacion']
        w('')
        w('=' * 70)
        w('  CHECKPOINT SUSCRIPCIONES / SYNC (SOLO LECTURA)')
        w('=' * 70)
        w(f'  Estado:       {estilo(reporte["estado"])}')
        w(f'  Checkpoint:   {reporte["checkpoint"]}')
        w(f'  Base:         {instalacion["database_alias"]}')
        w(f'  Tenant:       {instalacion["tenant_key"] or "(sin contexto)"}')
        w(f'  Sucursal:     {instalacion["sucursal_resuelta"] or instalacion["sucursal_codigo"] or "(sin resolver)"}')

        w('')
        w('PRESETS')
        for plan in reporte['planes_preset']:
            w(f'  {plan["slug"]:<14} {plan["estado"]}')

        w('')
        w('NEGOCIOS')
        if not reporte['negocios']['detalle']:
            w('  (ninguno)')
        for negocio in reporte['negocios']['detalle']:
            w(f'  {negocio["slug"]:<20} {negocio["estado"]}  plan={negocio["plan"] or "-"}')

        w('')
        if not reporte['problemas']:
            w(self.style.SUCCESS('  OK: no se detectó estado parcial.'))
        else:
            w(self.style.ERROR(f'PROBLEMAS ({len(reporte["problemas"])}):'))
            for problema in reporte['problemas']:
                w(self.style.ERROR(f'  - {problema["codigo"]}: {problema["mensaje"]}'))
        w('')
