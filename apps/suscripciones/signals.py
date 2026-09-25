"""
apps/suscripciones/signals.py
Invalidacion de cache del resolutor de modulos ante cualquier cambio de
plan / suscripcion / overrides / modulo.

SUS-011 — `post_save`/`post_delete`/`m2m_changed` corren DENTRO de la
transaccion que hizo el cambio. Publicar la invalidacion ahi mismo (bumpear
la version compartida) antes de que esa transaccion confirme deja dos
huecos:

1. Un lector concurrente, entre el bump y el commit, puede recachear el
   estado VIEJO bajo la version NUEVA -- y lo conserva por el TTL. Invalidar
   producia exactamente lo que intentaba evitar.
2. Si la transaccion se revierte, la invalidacion ya se publico: un cambio
   que nunca paso igual bumpea la version para todo el mundo.

Se difiere con `transaction.on_commit` (mismo patron que
`apps/permisos/signals.py`, PER-011): fuera de una transaccion atomica
corre de inmediato, asi que el autocommit no cambia.
"""
from django.db import transaction
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
    transaction.on_commit(invalidar_cache)


@receiver(m2m_changed, sender=Plan.modulos.through)
def _invalidar_m2m(sender, action, **kwargs):
    if action in ('post_add', 'post_remove', 'post_clear'):
        transaction.on_commit(invalidar_cache)
