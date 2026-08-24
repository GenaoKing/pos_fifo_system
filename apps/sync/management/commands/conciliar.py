"""
apps/sync/management/commands/conciliar.py

Fase 3 de docs/ROADMAP_SYNC_CONFIABLE.md: compara el estado AGREGADO de esta
sucursal (ventas, CxC, pagos por dia) contra lo que el cloud realmente tiene.

`verificar_sync` (Fase 0/1) responde "de lo que hay aca, que no tiene evento
de sync". Este comando responde una pregunta distinta y mas dura: "lo que el
cloud dice que tiene, coincide con lo que yo tengo". Un evento puede haber
viajado y el hecho en el cloud terminar distinto igual.

Uso:
    python manage.py conciliar
    python manage.py conciliar --dias=7
    python manage.py conciliar --json
    python manage.py conciliar --backfill --ejecutar
"""
import json as json_lib

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        'Compara agregados diarios (ventas, CxC, pagos) contra el cloud y '
        'reporta divergencias. Fase 3 de anti-entropia. Solo lectura salvo '
        '--backfill --ejecutar.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dias', type=int, default=30,
            help='Ventana de conciliacion en dias hacia atras. Default 30.',
        )
        parser.add_argument(
            '--json', action='store_true',
            help='Emite el resultado como JSON (para automatizar).',
        )
        parser.add_argument(
            '--backfill', action='store_true',
            help='Si hay divergencias, corre verificar_sync --backfill sobre '
                 'la misma ventana (repara BUG-A: hechos sin evento).',
        )
        parser.add_argument(
            '--ejecutar', action='store_true',
            help='Aplica el backfill. Sin esto, --backfill solo reporta que '
                 'haria (dry-run, igual que verificar_sync).',
        )

    def handle(self, *args, **opts):
        from apps.sync.conciliacion import conciliar

        resultado = conciliar(
            dias=opts['dias'],
            backfill=opts['backfill'],
            ejecutar=opts['ejecutar'],
        )

        if opts['json']:
            self.stdout.write(json_lib.dumps(resultado, indent=2, default=str))
        else:
            self._imprimir(resultado)

        if resultado['estado'] == 'ERROR':
            raise SystemExit(1)

    def _imprimir(self, r):
        w = self.stdout.write
        ok = self.style.SUCCESS
        warn = self.style.WARNING
        err = self.style.ERROR

        w('')
        w('=' * 70)
        w(f"  CONCILIACION  -  {r['desde']} .. {r['hasta']}  (tz {r['tz']})")
        w('=' * 70)
        w('')

        if r['estado'] == 'NO_SOPORTADO':
            w(warn(f"  {r['mensaje']}"))
            w('')
            w('=' * 70)
            return

        if r['estado'] == 'ERROR':
            w(err(f"  ERROR: {r['mensaje']}"))
            w('')
            w('=' * 70)
            return

        if r['estado'] == 'OK':
            w(ok(f"  {r['mensaje']}"))
        else:
            w(err(f"  {r['mensaje']}"))
            w('')
            for d in r['divergencias']:
                w(err(
                    f"  {d['tipo']:<12} {d['dia']}  {d['campo']:<8} "
                    f"local={d['local']}  cloud={d['cloud']}"
                ))

        backfill = r.get('backfill')
        if backfill is not None:
            w('')
            w('BACKFILL (verificar_sync --backfill sobre la misma ventana)')
            for linea in backfill['salida'].splitlines():
                w(f'  {linea}')
            if not backfill['ejecutado']:
                w(warn('  (dry-run: agregar --ejecutar para aplicar.)'))

        w('')
        w('=' * 70)
