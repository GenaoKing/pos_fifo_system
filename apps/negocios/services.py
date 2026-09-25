"""Ciclo de vida soportado de la identidad operativa ``Negocio``."""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.text import slugify

from apps.auditoria.models import Auditoria
from apps.auditoria.services import registrar_mutacion

from .models import Negocio


class NegocioLifecycleError(ValueError):
    pass


def _autorizar(actor, *, using, negocio=None):
    actor_db = getattr(getattr(actor, '_state', None), 'db', None)
    if actor is None or actor_db != using or not getattr(actor, 'activo', False):
        raise NegocioLifecycleError('Se requiere un actor activo de la misma BD.')
    if getattr(actor, 'is_superuser', False):
        return
    if negocio is None:
        raise NegocioLifecycleError('Solo un operador global crea negocios.')
    if actor.negocio_id != negocio.pk:
        raise NegocioLifecycleError('El actor pertenece a otro negocio.')
    if not actor.tiene_permiso('permisos.administrar'):
        raise NegocioLifecycleError('Sin permiso permisos.administrar.')


def _snapshot(negocio):
    return {
        'nombre': negocio.nombre,
        'slug': negocio.slug,
        'rnc': negocio.rnc_canonico,
        'activo': negocio.activo,
        'authority': 'TENANT_OPERATIONAL_PROJECTION',
    }


def crear_negocio(
    *, actor, nombre, rnc='', slug=None, canal=Auditoria.Canal.COMMAND,
    correlacion_id=None, using='default', max_intentos=5,
):
    """Crea una identidad logica con retry acotado ante carrera de slug."""
    _autorizar(actor, using=using)
    base = slugify(slug or nombre)[:110] or 'negocio'
    with transaction.atomic(using=using):
        ultimo_error = None
        for intento in range(1, max_intentos + 1):
            sufijo = '' if intento == 1 else f'-{intento}'
            candidato = f'{base[:120 - len(sufijo)]}{sufijo}'
            if Negocio.objects.using(using).filter(slug=candidato).exists():
                if slug is not None:
                    raise NegocioLifecycleError('El slug explicito ya existe.')
                continue
            negocio = Negocio(nombre=nombre, slug=candidato, rnc=rnc, activo=True)
            try:
                with transaction.atomic(using=using):
                    negocio.save(using=using)
            except IntegrityError as exc:
                ultimo_error = exc
                if slug is not None:
                    raise NegocioLifecycleError('El slug explicito ya existe.') from exc
                continue
            registrar_mutacion(
                accion='negocios.negocio.creado',
                actor=actor,
                entidad=negocio,
                antes={},
                despues=_snapshot(negocio),
                resultado=Auditoria.Resultado.SUCCEEDED,
                canal=canal,
                tenant=negocio.slug,
                correlacion_id=correlacion_id,
                using=using,
            )
            return negocio
        raise NegocioLifecycleError(
            f'No se pudo reservar slug tras {max_intentos} intentos.'
        ) from ultimo_error


def actualizar_negocio(
    *, negocio_id, actor, cambios, motivo, canal=Auditoria.Canal.COMMAND,
    correlacion_id=None, using='default',
):
    """Actualiza nombre/RNC/estado; slug nunca es una edicion ordinaria."""
    permitidos = {'nombre', 'rnc', 'activo'}
    desconocidos = set(cambios) - permitidos
    if desconocidos:
        raise NegocioLifecycleError(
            f'Campos no soportados: {sorted(desconocidos)}.'
        )
    if not (motivo or '').strip():
        raise NegocioLifecycleError('motivo es obligatorio.')

    with transaction.atomic(using=using):
        negocio = Negocio.objects.using(using).select_for_update().get(pk=negocio_id)
        _autorizar(actor, using=using, negocio=negocio)
        antes = _snapshot(negocio)
        for campo, valor in cambios.items():
            setattr(negocio, campo, valor)
        try:
            negocio.save(using=using)
        except ValidationError as exc:
            raise NegocioLifecycleError(str(exc)) from exc
        despues = _snapshot(negocio)
        if antes['activo'] != despues['activo']:
            accion = (
                'negocios.negocio.reactivado'
                if despues['activo'] else 'negocios.negocio.desactivado'
            )
        else:
            accion = 'negocios.negocio.actualizado'
        registrar_mutacion(
            accion=accion,
            actor=actor,
            entidad=negocio,
            antes=antes,
            despues=despues,
            resultado=Auditoria.Resultado.SUCCEEDED,
            canal=canal,
            tenant=negocio.slug,
            correlacion_id=correlacion_id,
            metadata={'motivo': motivo.strip()},
            using=using,
        )
    return negocio
