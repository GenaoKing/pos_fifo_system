"""
manage.py sync_permisos
Upsert del catalogo de permisos (apps/permisos/catalogo.py) en la tabla Permiso.

Idempotente. Correr tras agregar nuevos permisos al catalogo.
"""
from django.core.management.base import BaseCommand

from apps.permisos.catalogo import sembrar_catalogo
from apps.permisos.models import Permiso, Rol


class Command(BaseCommand):
    help = 'Sincroniza el catalogo de permisos en la base de datos.'

    def handle(self, *args, **options):
        creados, actualizados = sembrar_catalogo(Permiso)

        # Mantener completos los roles de sistema "Administrador": al crecer el
        # catalogo, re-otorgarles todo el catalogo (su intencion es acceso total).
        todos = list(Permiso.objects.all())
        admins = Rol.objects.filter(es_sistema=True, slug='administrador')
        for rol in admins:
            rol.permisos.set(todos)

        self.stdout.write(
            self.style.SUCCESS(
                f'Catalogo sincronizado: {creados} creados, {actualizados} actualizados '
                f'({Permiso.objects.count()} permisos en total). '
                f'Roles Administrador actualizados: {admins.count()}.'
            )
        )
