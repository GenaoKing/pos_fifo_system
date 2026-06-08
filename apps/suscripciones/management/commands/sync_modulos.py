"""manage.py sync_modulos — upsert del catalogo de modulos + planes default."""
from django.core.management.base import BaseCommand

from apps.suscripciones import seed
from apps.suscripciones.models import Modulo, Plan


class Command(BaseCommand):
    help = 'Sincroniza el catalogo de modulos y los planes default.'

    def handle(self, *args, **options):
        seed.sembrar_modulos(Modulo)
        seed.crear_planes_default(Plan, Modulo)
        self.stdout.write(self.style.SUCCESS(
            f'Modulos: {Modulo.objects.count()}, Planes: {Plan.objects.count()}.'
        ))
