"""Revisiones monotÃ³nicas del contrato CT-02."""
from django.db.models import F

from .models import EstadoRBAC


def obtener_estado_rbac(negocio, *, using=None):
    using = using or negocio._state.db or 'default'
    estado, _ = EstadoRBAC.objects.using(using).get_or_create(negocio=negocio)
    return estado


def leer_revisiones_rbac(negocio, *, using=None):
    """Lee sin crear filas; un tenant sin mutaciones parte de revision 1."""
    using = using or negocio._state.db or 'default'
    estado = EstadoRBAC.objects.using(using).filter(negocio=negocio).first()
    if estado is None:
        return 1, 1
    return estado.catalog_revision, estado.assignments_revision


def avanzar_revision_rbac(
    negocio_id, *, using, catalogo=False, asignaciones=True,
):
    if not negocio_id:
        return
    estado, _ = EstadoRBAC.objects.using(using).get_or_create(
        negocio_id=negocio_id,
    )
    cambios = {}
    if catalogo:
        cambios['catalog_revision'] = F('catalog_revision') + 1
    if asignaciones:
        cambios['assignments_revision'] = F('assignments_revision') + 1
    if cambios:
        EstadoRBAC.objects.using(using).filter(pk=estado.pk).update(**cambios)
