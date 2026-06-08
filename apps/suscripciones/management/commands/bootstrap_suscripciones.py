"""
manage.py bootstrap_suscripciones

Inicializa modulos + planes y, por cada negocio existente, crea su suscripcion
con los modulos derivados de sus flags de ConfiguracionNegocio (no cambia la
conducta actual). Idempotente.
"""
from django.core.management.base import BaseCommand

from apps.configuracion.models import ConfiguracionNegocio
from apps.negocios.models import Negocio
from apps.suscripciones import seed
from apps.suscripciones.models import Modulo, NegocioModulo, Plan, SuscripcionNegocio


class Command(BaseCommand):
    help = 'Inicializa modulos/planes y entitlements de los negocios existentes.'

    def handle(self, *args, **options):
        seed.bootstrap(
            ModuloModel=Modulo,
            PlanModel=Plan,
            NegocioModel=Negocio,
            NegocioModuloModel=NegocioModulo,
            SuscripcionModel=SuscripcionNegocio,
            ConfiguracionModel=ConfiguracionNegocio,
        )
        self.stdout.write(self.style.SUCCESS(
            f'Bootstrap OK. Negocios con suscripcion: '
            f'{SuscripcionNegocio.objects.count()}.'
        ))
