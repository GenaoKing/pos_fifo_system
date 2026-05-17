"""
apps/sync/management/commands/sincronizar.py

Management command que corre el sync engine.

Modos de uso:

    # Una pasada y salir (util para diagnostico o cron externo)
    python manage.py sincronizar --once

    # Loop continuo (usado como daemon via NSSM/Task Scheduler)
    python manage.py sincronizar

    # Solo push (emergencia: empujar eventos atrasados)
    python manage.py sincronizar --once --only-push

    # Solo pull (actualizar maestros sin tocar la cola de eventos)
    python manage.py sincronizar --once --only-pull

    # Forzar intervalo distinto al de settings
    python manage.py sincronizar --interval 30

Logging: los mensajes van a logger 'sync', que si el project tiene su
RotatingFileHandler configurado, iran tambien a sync.log. Si no, a consola.
"""
import logging
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger('sync')


class Command(BaseCommand):
    help = 'Corre el motor de sincronizacion con el cloud (push eventos + pull maestros).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--once',
            action='store_true',
            help='Corre una sola pasada y termina. Sin esto, corre en loop.',
        )
        parser.add_argument(
            '--interval',
            type=int,
            default=None,
            help='Segundos entre ciclos. Default: SYNC_INTERVAL (60).',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=None,
            help='Cantidad de eventos por push. Default: SYNC_BATCH_SIZE (50).',
        )
        parser.add_argument(
            '--only-push',
            action='store_true',
            help='Solo push de eventos, no pull de maestros.',
        )
        parser.add_argument(
            '--only-pull',
            action='store_true',
            help='Solo pull de maestros, no push de eventos.',
        )
        parser.add_argument(
            '--no-log',
            action='store_true',
            help='No grabar LogSync en BD (reduce ruido si corre muy seguido).',
        )

    def handle(self, *args, **opts):
        if not getattr(settings, 'SYNC_ENABLED', False):
            raise CommandError(
                'SYNC_ENABLED=False en settings. El sync esta deshabilitado. '
                'Activalo o corre en un settings de sucursal.'
            )

        # Importacion diferida (asegura que Django ya cargo)
        from apps.sync.engine import SyncEngine, SyncConfigError

        try:
            engine = SyncEngine(batch_size=opts.get('batch_size'))
        except Exception as exc:
            raise CommandError(f'No se pudo inicializar SyncEngine: {exc}')

        once = opts['once']
        interval = opts['interval'] or getattr(settings, 'SYNC_INTERVAL', 60)
        only_push = opts['only_push']
        only_pull = opts['only_pull']
        registrar_log = not opts['no_log']

        # Manejo de Ctrl-C y SIGTERM (cuando NSSM manda stop)
        self._running = True

        def handle_stop(signum, frame):
            self._running = False
            self.stdout.write(self.style.WARNING('\nSenial recibida, terminando...'))

        signal.signal(signal.SIGINT, handle_stop)
        signal.signal(signal.SIGTERM, handle_stop)

        self.stdout.write(self.style.SUCCESS(
            f'Sync engine arrancando. once={once} interval={interval}s '
            f'only_push={only_push} only_pull={only_pull}'
        ))
        self.stdout.write(f'Cloud: {engine.cloud_url}')
        self.stdout.write(f'Sucursal: {getattr(settings, "SUCURSAL_CODIGO", "N/A")}')

        while self._running:
            try:
                self._un_ciclo(engine, only_push, only_pull, registrar_log)
            except SyncConfigError as exc:
                raise CommandError(str(exc))
            except Exception as exc:
                logger.exception('Error inesperado en ciclo de sync: %s', exc)
                self.stderr.write(self.style.ERROR(f'Error: {exc}'))

            if once:
                break

            # Sleep interrumpible
            slept = 0
            while slept < interval and self._running:
                time.sleep(1)
                slept += 1

        self.stdout.write(self.style.SUCCESS('Sync engine detenido.'))

    def _un_ciclo(self, engine, only_push, only_pull, registrar_log):
        """Ejecuta un ciclo (push + pull segun flags) y reporta al stdout."""
        if not engine.check_connection():
            self.stdout.write(self.style.WARNING(
                f'[{self._now()}] Sin conexion al cloud. Esperando...'
            ))
            return

        if not only_pull:
            m = engine.push_eventos()
            style = self.style.SUCCESS if m['fallidos'] == 0 else self.style.WARNING
            self.stdout.write(style(
                f"[{self._now()}] PUSH procesados={m['procesados']} "
                f"confirmados={m['confirmados']} fallidos={m['fallidos']}"
            ))

        if not only_push:
            m = engine.pull_maestros()
            self.stdout.write(self.style.SUCCESS(
                f"[{self._now()}] PULL categorias={m['categorias']} "
                f"productos={m['productos']} clientes={m['clientes']}"
            ))

        # Registrar log si pidieron el ciclo completo
        if registrar_log and not (only_push or only_pull):
            try:
                from apps.sync.models import LogSync
                from apps.sucursales.models import get_sucursal_actual
                LogSync.objects.create(
                    tipo='FULL',
                    resultado='EXITOSO',
                    sucursal=get_sucursal_actual(),
                )
            except Exception as exc:
                logger.warning('No se pudo registrar LogSync: %s', exc)

    @staticmethod
    def _now():
        from datetime import datetime
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')