"""Repara de forma dirigida el historial fantasma de ``sucursales``.

No se invoca al arrancar ni desde una migracion. Sin ``--apply`` es un dry-run
que deja en stdout el target, las filas exactas y la accion propuesta para que
el operador pueda incorporarlo al acta de cambio.
"""

import json
from contextlib import contextmanager

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.tenancy.context import force_tenancy, tenant_context
from apps.tenancy.migration_repair import (
    desregistrar_migraciones_sucursales,
    inspeccionar_sucursales,
)
from apps.tenancy.models import Tenant
from apps.tenancy.registry import configure_tenant_database


class Command(BaseCommand):
    help = (
        'Repara el historial fantasma de sucursales en una sola base; sin '
        '--apply solo muestra el plan.'
    )

    def add_arguments(self, parser):
        destino = parser.add_mutually_exclusive_group(required=True)
        destino.add_argument(
            '--database',
            help='Alias exacto a reparar; para control plane use "default".',
        )
        destino.add_argument('--tenant', help='tenant_key exacto a reparar.')
        parser.add_argument(
            '--apply', action='store_true',
            help='Borra solo el historial de sucursales y reaplica esa app.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Fuerza el modo lectura (tambien es el modo por defecto).',
        )

    def handle(self, *args, **options):
        if options['apply'] and options['dry_run']:
            raise CommandError('Use --apply o --dry-run, no ambos.')

        with self._destino(options) as alias:
            estado = inspeccionar_sucursales(alias)
            self._imprimir_ledger(estado, apply=options['apply'])

            if not estado.reparacion_requerida:
                if estado.tabla_presente:
                    self.stdout.write(self.style.SUCCESS(
                        'Sin reparacion: sucursales_sucursal ya existe.'
                    ))
                    return
                raise CommandError(
                    f'{alias}: no hay historial de sucursales ni tabla. Es una '
                    'base sin inicializar; ejecute migrate, no este reparador.'
                )

            if not options['apply']:
                self.stdout.write(self.style.WARNING(
                    'DRY-RUN: no se escribio nada. Revise el ledger y repita '
                    'el mismo target con --apply para reparar.'
                ))
                return

            eliminadas = desregistrar_migraciones_sucursales(alias)
            self.stdout.write(
                f'APPLY: se desregistraron {eliminadas} filas de sucursales en {alias}.'
            )
            call_command(
                'migrate', 'sucursales', database=alias, interactive=False,
                verbosity=options.get('verbosity', 1),
            )
            final = inspeccionar_sucursales(alias)
            if not final.tabla_presente:
                raise CommandError(
                    f'{alias}: la reaplicacion termino sin sucursales_sucursal. '
                    'No continue con migrate_cloud/migrate_tenants; conserve este '
                    'ledger y restaure segun el handoff.'
                )
            self._imprimir_ledger(final, apply=True, resultado='REPARADA')
            self.stdout.write(self.style.SUCCESS(
                'REPARADA: ejecute ahora migrate_cloud --noinput o '
                'migrate_tenants --tenant <tenant> --incluir-inactivos --noinput, '
                'segun el target.'
            ))

    def _destino(self, options):
        database = options.get('database')
        if database:
            return self._contexto_database(database)

        tenant = Tenant.objects.using('default').filter(
            tenant_key=options['tenant'],
        ).first()
        if tenant is None:
            raise CommandError(f'Tenant "{options["tenant"]}" no existe.')
        return self._contexto_tenant(tenant)

    @contextmanager
    def _contexto_database(self, alias):
        from django.db import connections

        if alias not in connections:
            raise CommandError(f'El alias de base "{alias}" no esta configurado.')
        with force_tenancy(True):
            yield alias

    @contextmanager
    def _contexto_tenant(self, tenant):
        with force_tenancy(True):
            _, alias = configure_tenant_database(tenant, permitir_inactivo=True)
            with tenant_context(tenant, permitir_inactivo=True):
                yield alias

    def _imprimir_ledger(self, estado, *, apply, resultado='PENDIENTE'):
        ledger = {
            'accion': 'reparar_sucursales_dual_home',
            'alias': estado.alias,
            'apply': apply,
            'migraciones_registradas': list(estado.migraciones_registradas),
            'resultado': resultado,
            'tabla': 'sucursales_sucursal',
            'tabla_presente': estado.tabla_presente,
        }
        self.stdout.write('LEDGER ' + json.dumps(ledger, sort_keys=True))
