"""manage.py sync_modulos — upsert del catalogo de modulos y resincroniza los
planes default gestionados por preset (SUS-017)."""
from django.core.management.base import BaseCommand

from apps.suscripciones import seed
from apps.suscripciones.models import Modulo, Plan

_ETIQUETAS = {
    'creado': 'creado',
    'actualizado': 'actualizado (preset desactualizado, modulos resincronizados)',
    'sin_cambios': 'sin cambios (ya en la version vigente)',
    'personalizado': 'personalizado, sin tocar (preset_version vacio)',
}


class Command(BaseCommand):
    help = (
        'Sincroniza el catalogo de modulos y resincroniza los planes '
        'default (Basico/Pro/Empresarial) que sigan gestionados por preset.'
    )

    def handle(self, *args, **options):
        seed.sembrar_modulos(Modulo)

        resultados = []

        def _reportar(slug, accion):
            resultados.append((slug, accion))

        seed.sincronizar_planes_preset(Plan, Modulo, reportar=_reportar)

        for slug, accion in resultados:
            self.stdout.write(f'  Plan {slug}: {_ETIQUETAS[accion]}')

        self.stdout.write(self.style.SUCCESS(
            f'Modulos: {Modulo.objects.count()}, Planes: {Plan.objects.count()}.'
        ))
