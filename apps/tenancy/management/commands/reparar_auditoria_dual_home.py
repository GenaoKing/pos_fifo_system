"""Explicit repair for the known legacy control-plane audit history only."""
import json

from django.core.management.base import BaseCommand, CommandError

from apps.tenancy.migration_repair import (
    inspeccionar_auditoria_control,
    materializar_auditoria_control_fantasma,
    plan_auditoria_control,
)


class Command(BaseCommand):
    help = (
        'Materializa la tabla historica de auditoria solo en el control plane '
        'legacy 0001..0004; sin --apply es solo lectura.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--database', required=True, choices=['default'])
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument('--apply', action='store_true')
        mode.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        if options['apply'] and options['dry_run']:
            raise CommandError('Use --apply o --dry-run, no ambos.')
        estado = inspeccionar_auditoria_control(options['database'])
        if estado.tabla_presente:
            self.stdout.write(self.style.SUCCESS(
                'Sin reparacion: auditoria_auditoria ya existe. No se modifico nada.'
            ))
            return
        _, plan = plan_auditoria_control(estado)
        self.stdout.write('LEDGER ' + json.dumps({**plan, 'apply': options['apply']}, sort_keys=True))
        if not options['apply']:
            self.stdout.write(self.style.WARNING(
                'DRY-RUN: no se escribio nada. Revise el ledger y repita con --apply.'
            ))
            return
        materializar_auditoria_control_fantasma(estado.alias, estado.migraciones_registradas)
        self.stdout.write('LEDGER ' + json.dumps({**plan, 'apply': True, 'resultado': 'REPARADA'}, sort_keys=True))
        self.stdout.write(self.style.SUCCESS(
            'REPARADA: tabla historica materializada; django_migrations intacto. '
            'No se aplicaron migraciones pendientes. Continue con migrate_cloud --noinput.'
        ))
