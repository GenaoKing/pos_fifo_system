"""
apps/suscripciones/signals.py
Invalidacion de cache del resolutor de modulos ante cualquier cambio de
plan / suscripcion / overrides / modulo.
"""
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from .engine import invalidar_cache
from .models import (
    Modulo,
    NegocioModulo,
    Plan,
    SucursalModuloOverride,
    SuscripcionNegocio,
)


@receiver([post_save, post_delete], sender=Plan)
@receiver([post_save, post_delete], sender=SuscripcionNegocio)
@receiver([post_save, post_delete], sender=NegocioModulo)
@receiver([post_save, post_delete], sender=SucursalModuloOverride)
@receiver([post_save, post_delete], sender=Modulo)
def _invalidar(sender, **kwargs):
    invalidar_cache()


@receiver(m2m_changed, sender=Plan.modulos.through)
def _invalidar_m2m(sender, action, **kwargs):
    if action in ('post_add', 'post_remove', 'post_clear'):
        invalidar_cache()
