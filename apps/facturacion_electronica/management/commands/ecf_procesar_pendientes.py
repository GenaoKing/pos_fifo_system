"""
apps/facturacion_electronica/management/commands/ecf_procesar_pendientes.py

Management command que procesa la cola de ECFs.

Modos de operación:
- Sin flags: procesa ambas cosas en este orden:
    1. ECFs en PENDIENTE/ERROR (emite contra MSeller)
    2. ECFs en ENVIADO/EN_PROCESO (consulta estado en MSeller)
- --solo-emitir: solo el primer paso
- --solo-consultar: solo el segundo paso
- --limite N: procesa máximo N ECFs por modo (default 100)
- --ecf-id ID: procesa un ECF específico (útil para debugging)
- --dry-run: lee la cola pero no procesa, solo muestra qué haría

Cómo se ejecuta en producción:
    Vía Task Scheduler de Windows, cada 30 segundos:
        python manage.py ecf_procesar_pendientes

    En desarrollo, manualmente:
        python manage.py ecf_procesar_pendientes
        python manage.py ecf_procesar_pendientes --dry-run
        python manage.py ecf_procesar_pendientes --solo-consultar --limite 10
        python manage.py ecf_procesar_pendientes --ecf-id 42

Concurrency:
    Usa select_for_update(skip_locked=True) para que dos instancias
    accidentales del comando no se peleen por el mismo ECF. PostgreSQL
    skip_locked es no-bloqueante: el segundo proceso ve la fila como
    si no existiera y pasa al siguiente.

Logging:
    Logger 'ecf.procesador' debe estar configurado en settings con
    rotación a archivo (ej: logs/ecf_procesador.log). Si no, los
    mensajes salen a stderr.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.facturacion_electronica.interfaces import EstadosECF
from apps.facturacion_electronica.models import ECF
from apps.facturacion_electronica.services.procesador import (
    ResultadoProcesamiento,
    procesar_ecf,
)

logger = logging.getLogger('ecf.procesador')


# Estados que el procesador puede avanzar
ESTADOS_PARA_EMITIR = (EstadosECF.PENDIENTE, EstadosECF.ERROR)
ESTADOS_PARA_CONSULTAR = (EstadosECF.ENVIADO, EstadosECF.EN_PROCESO)


class Command(BaseCommand):
    help = (
        'Procesa la cola de ECFs: emite los pendientes contra MSeller '
        'y consulta el estado de los que ya fueron enviados.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--solo-emitir',
            action='store_true',
            help='Solo procesa ECFs en PENDIENTE/ERROR (no consulta estados).',
        )
        parser.add_argument(
            '--solo-consultar',
            action='store_true',
            help='Solo consulta ECFs en ENVIADO/EN_PROCESO (no emite nuevos).',
        )
        parser.add_argument(
            '--limite',
            type=int,
            default=100,
            help='Máximo de ECFs a procesar por modo. Default: 100.',
        )
        parser.add_argument(
            '--ecf-id',
            type=int,
            default=None,
            help='ID específico de ECF a procesar (ignora estado y límite).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='No procesa: solo lista los ECFs que serían procesados.',
        )

    def handle(self, *args, **options):
        # Validar combinación de flags
        if options['solo_emitir'] and options['solo_consultar']:
            raise CommandError(
                'No se pueden combinar --solo-emitir y --solo-consultar.'
            )

        ts_inicio = time.monotonic()

        # Modo single-ECF (debugging)
        if options['ecf_id']:
            self._procesar_uno(options['ecf_id'], dry_run=options['dry_run'])
            return

        # Modo batch
        contadores = {
            'emitidos_ok': 0,
            'emitidos_fallo': 0,
            'consultados_ok': 0,
            'consultados_fallo': 0,
        }

        if not options['solo_consultar']:
            self.stdout.write('=== Fase 1: emisión ===')
            self._procesar_batch(
                estados=ESTADOS_PARA_EMITIR,
                limite=options['limite'],
                dry_run=options['dry_run'],
                contadores=contadores,
                clave_ok='emitidos_ok',
                clave_fallo='emitidos_fallo',
            )

        if not options['solo_emitir']:
            self.stdout.write('=== Fase 2: consulta de estado ===')
            self._procesar_batch(
                estados=ESTADOS_PARA_CONSULTAR,
                limite=options['limite'],
                dry_run=options['dry_run'],
                contadores=contadores,
                clave_ok='consultados_ok',
                clave_fallo='consultados_fallo',
            )

        elapsed = time.monotonic() - ts_inicio
        resumen = (
            f'Resumen: '
            f'emitidos OK={contadores["emitidos_ok"]} '
            f'fallos={contadores["emitidos_fallo"]} | '
            f'consultados OK={contadores["consultados_ok"]} '
            f'fallos={contadores["consultados_fallo"]} | '
            f'tiempo={elapsed:.2f}s'
        )
        self.stdout.write(self.style.SUCCESS(resumen))
        logger.info(resumen)

    # ------------------------------------------------------------------ helpers

    def _procesar_uno(self, ecf_id: int, *, dry_run: bool) -> None:
        """Modo single-ECF para debugging. No usa select_for_update."""
        try:
            ecf = ECF.objects.get(id=ecf_id)
        except ECF.DoesNotExist:
            raise CommandError(f'ECF#{ecf_id} no existe.')

        self.stdout.write(
            f'ECF#{ecf.id} estado={ecf.estado} tipo={ecf.tipo} '
            f'intentos={ecf.intentos} encf={ecf.encf or "(sin asignar)"}'
        )

        if dry_run:
            self.stdout.write(self.style.WARNING('--dry-run: no se procesa.'))
            return

        resultado = procesar_ecf(ecf)
        self.stdout.write(repr(resultado))

    def _procesar_batch(
        self,
        *,
        estados: tuple[str, ...],
        limite: int,
        dry_run: bool,
        contadores: dict,
        clave_ok: str,
        clave_fallo: str,
    ) -> None:
        """
        Procesa hasta `limite` ECFs en los estados dados.

        Usa select_for_update(skip_locked=True) en una transacción
        corta solo para "tomar" los IDs. Después procesa cada uno
        con su propia transacción dentro de procesar_ecf(). Esto
        evita mantener locks largos durante las llamadas HTTP a
        MSeller.
        """
        ids_a_procesar = self._tomar_ids(estados=estados, limite=limite)

        if not ids_a_procesar:
            self.stdout.write('  (cola vacía)')
            return

        self.stdout.write(f'  encontrados {len(ids_a_procesar)} ECFs')

        if dry_run:
            for ecf_id in ids_a_procesar:
                ecf = ECF.objects.get(id=ecf_id)
                self.stdout.write(
                    f'  [DRY-RUN] ECF#{ecf.id} estado={ecf.estado} '
                    f'tipo={ecf.tipo} intentos={ecf.intentos}'
                )
            return

        for ecf_id in ids_a_procesar:
            # Refrescar desde BD por si cambió entre el SELECT y el
            # procesamiento (otra instancia, o la propia venta cambió
            # de estado a ANULADA).
            try:
                ecf = ECF.objects.select_related('venta', 'emisor').get(id=ecf_id)
            except ECF.DoesNotExist:
                continue  # raro pero defensivo

            try:
                resultado = procesar_ecf(ecf)
            except Exception as exc:
                # procesar_ecf() no debería levantar, pero por
                # defensa: si lo hace, no rompemos el batch entero.
                logger.exception(
                    f'Error inesperado procesando ECF#{ecf_id}: {exc}'
                )
                contadores[clave_fallo] += 1
                self.stdout.write(self.style.ERROR(
                    f'  ✗ ECF#{ecf_id} excepción no esperada: {exc}'
                ))
                continue

            if resultado.exitoso:
                contadores[clave_ok] += 1
                self.stdout.write(f'  {resultado!r}')
            else:
                contadores[clave_fallo] += 1
                self.stdout.write(self.style.WARNING(f'  {resultado!r}'))

    def _tomar_ids(
        self,
        *,
        estados: tuple[str, ...],
        limite: int,
    ) -> list[int]:
        """
        Toma los IDs de los próximos `limite` ECFs reintentables en
        los estados dados, usando skip_locked para concurrency.

        Filtra `intentos < 5` aquí para no traer ECFs que el procesador
        va a descartar igual por límite de intentos.
        """
        with transaction.atomic():
            qs = (
                ECF.objects
                .select_for_update(skip_locked=True)
                .filter(estado__in=estados, intentos__lt=5)
                .order_by('creado_en')
                .values_list('id', flat=True)[:limite]
            )
            return list(qs)