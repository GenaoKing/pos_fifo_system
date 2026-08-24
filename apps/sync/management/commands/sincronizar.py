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
from django.utils import timezone

from apps.sync.engine import clasificar_ciclo

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
        parser.add_argument(
            '--sin-conciliacion',
            action='store_true',
            help='No correr la conciliacion diaria (Fase 3) en este loop.',
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

        self._sin_conciliacion = opts['sin_conciliacion'] or only_push or only_pull
        # Fecha local (no datetime) de la ultima corrida de conciliacion en
        # ESTE proceso. Vive en memoria, no en BD: si el servicio reinicia a
        # mitad de dia, corre una vez de mas, que es inocuo (es solo lectura
        # salvo que alguien pida --backfill a mano).
        self._conciliacion_ultimo_dia = None

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

            if not self._sin_conciliacion:
                # Fuera del try/except del ciclo: un fallo aca no debe
                # contaminar el LogSync FULL del ciclo de sync, y viceversa.
                self._conciliacion_diaria(engine)

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

        heartbeat_ok = engine.heartbeat()
        hb_style = self.style.SUCCESS if heartbeat_ok else self.style.WARNING
        self.stdout.write(hb_style(
            f'[{self._now()}] HEARTBEAT {"ok" if heartbeat_ok else "fallo"}'
        ))

        push = {'procesados': 0, 'confirmados': 0, 'fallidos': 0}
        pull = {'total': 0, 'ok': True, 'errores': [], 'bloqueos': []}

        if not only_pull:
            push = engine.push_eventos()
            style = self.style.SUCCESS if push['fallidos'] == 0 else self.style.WARNING
            self.stdout.write(style(
                f"[{self._now()}] PUSH procesados={push['procesados']} "
                f"confirmados={push['confirmados']} fallidos={push['fallidos']}"
            ))

        if not only_push:
            pull = engine.pull_maestros()
            # El estilo sale del resultado, no de la costumbre: antes el pull se
            # imprimia SIEMPRE en verde aunque todas las entidades hubieran
            # fallado con 401.
            style = self.style.SUCCESS if pull.get('ok', True) else self.style.ERROR
            self.stdout.write(style(
                f"[{self._now()}] PULL categorias={pull['categorias']} "
                f"productos={pull['productos']} clientes={pull['clientes']} "
                f"roles={pull.get('roles', 0)} asignaciones={pull.get('asignaciones', 0)} "
                f"metodos_credito={pull.get('metodos_credito', 0)} "
                f"configuracion={pull.get('configuracion', 0)}"
            ))
            for error in pull.get('errores', []):
                self.stdout.write(self.style.ERROR(f'    ! {error}'))
            for bloqueo in pull.get('bloqueos', []):
                self.stdout.write(self.style.WARNING(f'    ~ {bloqueo}'))

        # Registrar log si pidieron el ciclo completo
        if registrar_log and not (only_push or only_pull):
            estado, motivos = clasificar_ciclo(
                heartbeat=heartbeat_ok, push=push, pull=pull,
            )
            if estado != 'EXITOSO':
                self.stdout.write(self.style.WARNING(
                    f'[{self._now()}] CICLO {estado}: {"; ".join(motivos)}'
                ))
            try:
                from apps.sync.models import LogSync
                from apps.sucursales.models import get_sucursal_actual
                LogSync.objects.create(
                    tipo='FULL',
                    resultado=estado,
                    mensaje='; '.join(motivos)[:2000],
                    eventos_procesados=push['procesados'],
                    eventos_exitosos=push['confirmados'],
                    eventos_fallidos=push['fallidos'],
                    registros_descargados=pull.get('total', 0),
                    sucursal=get_sucursal_actual(),
                )
            except Exception as exc:
                logger.warning('No se pudo registrar LogSync: %s', exc)

    def _conciliacion_diaria(self, engine):
        """
        Corre `conciliar` como mucho una vez por dia local, a partir de
        SYNC_CONCILIACION_HORA. Solo DETECTA -- nunca corre --backfill sola:
        reparar automaticamente sin que nadie mire el resultado es exactamente
        el tipo de "arreglo silencioso" que este roadmap viene evitando desde
        la Fase 1. Un operador decide con `conciliar --backfill --ejecutar`.

        Cualquier excepcion se atrapa aca: un fallo en la conciliacion no debe
        tumbar el loop de push/pull, que es lo que de verdad mantiene vivo el
        negocio.
        """
        if not getattr(settings, 'SYNC_CONCILIACION_ENABLED', True):
            return

        ahora = timezone.localtime()
        hoy = ahora.date()
        if self._conciliacion_ultimo_dia == hoy:
            return
        hora_minima = getattr(settings, 'SYNC_CONCILIACION_HORA', 6)
        if ahora.hour < hora_minima:
            return

        try:
            from apps.sync.conciliacion import conciliar
            from apps.sync.models import LogSync
            from apps.sucursales.models import get_sucursal_actual

            dias = getattr(settings, 'SYNC_CONCILIACION_DIAS', 30)
            resultado = conciliar(dias=dias)

            self._conciliacion_ultimo_dia = hoy

            if resultado['estado'] == 'NO_SOPORTADO':
                self.stdout.write(f"[{self._now()}] CONCILIACION: {resultado['mensaje']}")
                return

            resultado_log = {
                'OK': 'EXITOSO', 'DIVERGENTE': 'PARCIAL', 'ERROR': 'FALLO',
            }[resultado['estado']]
            estilo = self.style.SUCCESS if resultado['estado'] == 'OK' else self.style.WARNING
            self.stdout.write(estilo(f"[{self._now()}] CONCILIACION: {resultado['mensaje']}"))

            LogSync.objects.create(
                tipo='CONCILIACION',
                resultado=resultado_log,
                mensaje=resultado['mensaje'][:5000],
                detalle=resultado['divergencias'] or None,
                sucursal=get_sucursal_actual(),
            )
        except Exception as exc:
            logger.exception('Conciliacion diaria fallo: %s', exc)
            self.stdout.write(self.style.ERROR(f'[{self._now()}] CONCILIACION: error: {exc}'))

    @staticmethod
    def _now():
        from datetime import datetime
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
