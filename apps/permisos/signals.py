"""
apps/permisos/signals.py
Invalidacion de cache del motor de permisos.

Cualquier cambio que altere los permisos efectivos de algun usuario bumpea la
version global del cache (engine.invalidar_cache), de forma que la siguiente
consulta recalcula. Los cambios de roles/permisos son poco frecuentes (un admin
reconfigurando), asi que invalidar todo es aceptable y portable.

Dos correcciones sobre la version anterior (PER-010, PER-011):

1. **Se observan tambien Usuario, Negocio y Sucursal.** Antes solo Rol,
   AsignacionRol, Permiso y el M2M. Degradar a un usuario de ADMIN a CAJERA,
   desactivarlo, moverlo de negocio o desactivar su negocio no cambiaba la
   version: el catalogo precargado seguia autorizandolo hasta que expirara el
   TTL. Se reprodujo en la auditoria.

2. **La invalidacion espera al commit.** `post_save` corre DENTRO de la
   transaccion; entre el bump y el commit, cualquier lectura recacheaba el
   estado VIEJO y lo dejaba fijo por el TTL — es decir, invalidar podia
   producir exactamente lo que intentaba evitar. Ahora se difiere con
   `transaction.on_commit`, y si la transaccion se revierte no se invalida
   nada, que es lo correcto.
"""
from django.db import transaction
from django.db.models import F
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from .engine import invalidar_cache, limpiar_memo
from .models import AsignacionRol, Permiso, Rol
from .revisions import avanzar_revision_rbac


def _invalidar_tras_commit():
    """
    Limpia lo local YA, y publica la invalidacion global al hacer commit.

    Los dos tiempos son distintos a proposito:

    - **El memo del request se descarta de inmediato.** Es privado de este
      contexto de ejecucion, asi que vaciarlo no puede publicar nada a medias:
      solo obliga a releer, y dentro de la transaccion se lee la propia
      escritura. Sin esto, quien acaba de cambiar un rol seguiria viendo el set
      anterior durante el resto del request.

    - **El bump de la version global espera al commit.** Corriendo dentro de la
      transaccion, entre el bump y el commit cualquier lectura recacheaba el
      estado VIEJO bajo la version NUEVA y lo dejaba fijo por el TTL: invalidar
      producia justo lo que intentaba evitar (PER-011). Y si la transaccion se
      revierte, no se invalida nada, que es lo correcto.

    `on_commit` fuera de una transaccion atomica ejecuta de inmediato, asi que
    esto tambien funciona en autocommit.
    """
    limpiar_memo()
    transaction.on_commit(invalidar_cache)


@receiver([post_save, post_delete], sender=Permiso)
def _invalidar_en_cambio(sender, using, **kwargs):
    from apps.negocios.models import Negocio

    for negocio_id in Negocio.objects.using(using).values_list('id', flat=True):
        avanzar_revision_rbac(
            negocio_id,
            using=using,
            catalogo=True,
            asignaciones=False,
        )
    _invalidar_tras_commit()


@receiver([post_save, post_delete], sender=Rol)
def _cambio_rol(sender, instance, using, **kwargs):
    avanzar_revision_rbac(instance.negocio_id, using=using)
    _invalidar_tras_commit()


@receiver([post_save, post_delete], sender=AsignacionRol)
def _cambio_asignacion(sender, instance, using, **kwargs):
    negocio_id = None
    if instance.rol_id:
        negocio_id = (
            Rol.objects.using(using).filter(pk=instance.rol_id)
            .values_list('negocio_id', flat=True).first()
        )
    avanzar_revision_rbac(negocio_id, using=using)
    _invalidar_tras_commit()


@receiver(m2m_changed, sender=Rol.permisos.through)
def _invalidar_en_cambio_permisos_rol(sender, action, **kwargs):
    instance = kwargs['instance']
    using = kwargs['using']
    reverse = kwargs.get('reverse', False)

    if action == 'pre_clear' and reverse:
        instance._rbac_roles_pre_clear = list(
            instance.roles.using(using).values_list('pk', flat=True)
        )
        return

    if action in ('post_add', 'post_remove', 'post_clear'):
        if reverse:
            role_ids = kwargs.get('pk_set') or getattr(
                instance, '_rbac_roles_pre_clear', (),
            )
            roles = list(
                Rol.objects.using(using)
                .filter(pk__in=role_ids)
                .values_list('pk', 'negocio_id')
            )
        else:
            roles = [(instance.pk, instance.negocio_id)]

        preservar = (
            not reverse
            and getattr(instance, '_preserve_rbac_revision', False)
        )
        if not preservar:
            Rol.objects.using(using).filter(
                pk__in=[rol_id for rol_id, _ in roles],
            ).update(
                revision=F('revision') + 1,
                fecha_modificacion=timezone.now(),
            )
        if not reverse:
            instance.refresh_from_db(
                using=using, fields=['revision', 'fecha_modificacion'],
            )
        for negocio_id in {negocio_id for _, negocio_id in roles}:
            avanzar_revision_rbac(negocio_id, using=using)
        if hasattr(instance, '_rbac_roles_pre_clear'):
            del instance._rbac_roles_pre_clear
        _invalidar_tras_commit()


# --- Cambios fuera de apps/permisos que alteran permisos efectivos ---------
#
# El motor lee `usuario.activo`, `usuario.negocio_id`, `usuario.rol` (acceso
# total legacy), `negocio.activo` y `sucursal.activa`. Todos viven en otras
# apps, y ninguno invalidaba nada.

def _conectar_modelos_externos():
    from django.apps import apps as django_apps

    for etiqueta in ('usuarios.Usuario', 'negocios.Negocio', 'sucursales.Sucursal'):
        try:
            modelo = django_apps.get_model(etiqueta)
        except (LookupError, ValueError):  # pragma: no cover - app ausente
            continue
        post_save.connect(
            _invalidar_externo, sender=modelo,
            dispatch_uid=f'permisos_invalidar_{etiqueta}_save',
        )
        post_delete.connect(
            _invalidar_externo, sender=modelo,
            dispatch_uid=f'permisos_invalidar_{etiqueta}_delete',
        )


def _invalidar_externo(sender, **kwargs):
    _invalidar_tras_commit()


_conectar_modelos_externos()
