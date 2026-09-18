"""Mutaciones soportadas del ciclo de vida de sucursales."""
from django.db import transaction

from apps.auditoria.models import Auditoria
from apps.auditoria.services import registrar_mutacion

from .models import Sucursal


class SucursalMutationError(ValueError):
    pass


_CAMPOS_EDITABLES = {'nombre', 'direccion', 'telefono', 'activa'}


def _db_de(obj):
    return getattr(getattr(obj, '_state', None), 'db', None)


def _tenant_auditoria(negocio, using):
    if using.startswith('tnt_'):
        from apps.tenancy.context import get_current_tenant_key

        return get_current_tenant_key()
    return negocio.slug if negocio is not None else None


def _snapshot(sucursal):
    return {
        'codigo': sucursal.codigo,
        'nombre': sucursal.nombre,
        'direccion': sucursal.direccion,
        'telefono': sucursal.telefono,
        'activa': sucursal.activa,
    }


def _autorizar(actor, negocio, using):
    if actor is None or _db_de(actor) != using:
        raise SucursalMutationError('El actor debe pertenecer a la BD tenant.')
    if not getattr(actor, 'activo', False):
        raise SucursalMutationError('El actor esta inactivo.')
    if not getattr(actor, 'is_superuser', False) and actor.negocio_id != negocio.pk:
        raise SucursalMutationError('El actor pertenece a otro negocio.')
    # La topologia del negocio no se delega a un rol acotado a una sucursal.
    if not actor.tiene_permiso('permisos.administrar', sucursal=None):
        raise SucursalMutationError('Sin permiso global permisos.administrar.')


def crear_sucursal(*, actor, negocio, cambios, correlacion_id=None, using):
    """Crea una sucursal tenant-scoped y deja CT-01 sin exponer su API key."""
    if not using or _db_de(negocio) != using:
        raise SucursalMutationError('Negocio y escritura deben usar la misma BD.')
    desconocidos = set(cambios) - (_CAMPOS_EDITABLES | {'codigo'})
    if desconocidos:
        raise SucursalMutationError(f'Campos no soportados: {sorted(desconocidos)}.')
    codigo = (cambios.get('codigo') or '').strip().upper()
    if not codigo:
        raise SucursalMutationError('codigo es obligatorio.')
    _autorizar(actor, negocio, using)

    with transaction.atomic(using=using):
        if Sucursal.objects.using(using).filter(codigo__iexact=codigo).exists():
            raise SucursalMutationError('El codigo de sucursal ya existe.')
        sucursal = Sucursal(
            negocio=negocio,
            codigo=codigo,
            **{campo: cambios[campo] for campo in _CAMPOS_EDITABLES if campo in cambios},
        )
        sucursal.full_clean()
        sucursal.save(using=using)
        registrar_mutacion(
            accion='sucursales.sucursal.creada',
            actor=actor,
            entidad=sucursal,
            antes={},
            despues=_snapshot(sucursal),
            resultado=Auditoria.Resultado.SUCCEEDED,
            canal=Auditoria.Canal.PORTAL_API,
            tenant=_tenant_auditoria(negocio, using),
            sucursal=sucursal,
            correlacion_id=correlacion_id,
            using=using,
        )
    return sucursal


def actualizar_sucursal(*, sucursal_id, actor, cambios, motivo, correlacion_id=None, using):
    """Actualiza atributos no identitarios; el codigo es inmutable por sync."""
    desconocidos = set(cambios) - _CAMPOS_EDITABLES
    if desconocidos:
        raise SucursalMutationError(f'Campos no soportados: {sorted(desconocidos)}.')
    if not motivo or not motivo.strip():
        raise SucursalMutationError('motivo es obligatorio.')

    with transaction.atomic(using=using):
        sucursal = Sucursal.objects.using(using).select_for_update().get(pk=sucursal_id)
        _autorizar(actor, sucursal.negocio, using)
        antes = _snapshot(sucursal)
        for campo, valor in cambios.items():
            setattr(sucursal, campo, valor)
        sucursal.full_clean()
        despues = _snapshot(sucursal)
        if antes == despues:
            return sucursal
        sucursal.save(using=using, update_fields=[*cambios, 'fecha_modificacion'])
        accion = 'sucursales.sucursal.actualizada'
        if antes['activa'] != despues['activa']:
            accion = (
                'sucursales.sucursal.activada'
                if despues['activa'] else 'sucursales.sucursal.desactivada'
            )
        registrar_mutacion(
            accion=accion,
            actor=actor,
            entidad=sucursal,
            antes=antes,
            despues=despues,
            resultado=Auditoria.Resultado.SUCCEEDED,
            canal=Auditoria.Canal.PORTAL_API,
            tenant=_tenant_auditoria(sucursal.negocio, using),
            sucursal=sucursal,
            correlacion_id=correlacion_id,
            metadata={'motivo': motivo.strip()},
            using=using,
        )
    return sucursal


def desactivar_sucursal(*, sucursal_id, actor, motivo, correlacion_id=None, using):
    """Baja lógica e idempotente: conserva operaciones y corta sync por estado."""
    return actualizar_sucursal(
        sucursal_id=sucursal_id,
        actor=actor,
        cambios={'activa': False},
        motivo=motivo,
        correlacion_id=correlacion_id,
        using=using,
    )
