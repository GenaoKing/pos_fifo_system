"""
apps/permisos/signals.py
Invalidacion de cache del motor de permisos.

Cualquier cambio que altere los permisos efectivos de algun usuario bumpea la
version global del cache (engine.invalidar_cache), de forma que la siguiente
consulta recalcula. Los cambios de roles/permisos son poco frecuentes (un admin
reconfigurando), asi que invalidar todo es aceptable y portable.
"""
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from .engine import invalidar_cache
from .models import AsignacionRol, Permiso, Rol


@receiver([post_save, post_delete], sender=Rol)
@receiver([post_save, post_delete], sender=AsignacionRol)
@receiver([post_save, post_delete], sender=Permiso)
def _invalidar_en_cambio(sender, **kwargs):
    invalidar_cache()


@receiver(m2m_changed, sender=Rol.permisos.through)
def _invalidar_en_cambio_permisos_rol(sender, action, **kwargs):
    if action in ('post_add', 'post_remove', 'post_clear'):
        invalidar_cache()
