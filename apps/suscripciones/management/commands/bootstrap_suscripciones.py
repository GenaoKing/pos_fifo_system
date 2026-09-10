"""
manage.py bootstrap_suscripciones [--dry-run]

Inicializa modulos + planes y, por cada negocio existente, crea su suscripcion
con los modulos derivados de sus flags de ConfiguracionNegocio (no cambia la
conducta actual). Preserva la conducta por sucursal (SUS-008) y adopta la
configuracion legacy sin sucursal solo cuando es inequivoco (SUS-009).
Idempotente y atomico (SUS-016).

`--dry-run` simula el bootstrap dentro de una transaccion y la revierte: reporta
que HARIA sin escribir nada.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.configuracion.models import ConfiguracionNegocio
from apps.negocios.models import Negocio
from apps.suscripciones import seed
from apps.suscripciones.models import Modulo, NegocioModulo, Plan, SuscripcionNegocio


class Command(BaseCommand):
    help = 'Inicializa modulos/planes y entitlements de los negocios existentes.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Simula el bootstrap y revierte la transaccion (no escribe).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        try:
            with transaction.atomic():
                resumen = seed.bootstrap(
                    ModuloModel=Modulo,
                    PlanModel=Plan,
                    NegocioModel=Negocio,
                    NegocioModuloModel=NegocioModulo,
                    SuscripcionModel=SuscripcionNegocio,
                    ConfiguracionModel=ConfiguracionNegocio,
                )
                if dry_run:
                    transaction.set_rollback(True)
        except seed.BootstrapAmbiguo as exc:
            # SUS-009: ambiguedad = no se escribe nada y se falla en voz alta.
            raise CommandError(str(exc))

        prefijo = '[DRY-RUN] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(
            f'{prefijo}Bootstrap OK. '
            f'Negocios: {resumen["negocios"]}, '
            f'suscripciones creadas: {resumen["suscripciones_creadas"]}, '
            f'overrides de negocio: {resumen["negocio_modulos"]}, '
            f'overrides de sucursal (preservacion): {resumen["overrides_sucursal"]}, '
            f'configs legacy adoptadas: {resumen["legacy_adoptadas"]}.'
        ))
        if not dry_run:
            self.stdout.write(
                f'Negocios con suscripcion: {SuscripcionNegocio.objects.count()}.'
            )
