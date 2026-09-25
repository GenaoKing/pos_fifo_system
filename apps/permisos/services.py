"""Mutaciones transaccionales de RBAC y productores CT-01."""
from __future__ import annotations

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.text import slugify

from apps.auditoria.models import Auditoria
from apps.auditoria.services import registrar_mutacion

from .catalogo import codigos_catalogo
from .models import AsignacionRol, Permiso, Rol


class RBACMutationError(ValueError):
    pass


class RBACRevisionConflict(RBACMutationError):
    pass


def _db_de(obj):
    return getattr(getattr(obj, '_state', None), 'db', None)


def _tenant_auditoria(negocio, using):
    """Prefer the technical tenant identity over the business slug in cloud."""
    from apps.tenancy.context import get_current_tenant_key

    tenant_key = get_current_tenant_key()
    if using.startswith('tnt_'):
        # A tenant database without an active context must fail in CT-01.
        return tenant_key
    return tenant_key or negocio.slug


def _snapshot_rol(rol):
    return {
        'cloud_id': str(rol.cloud_id),
        'revision': rol.revision,
        'negocio_id': rol.negocio_id,
        'slug': rol.slug,
        'nombre': rol.nombre,
        'descripcion': rol.descripcion,
        'es_sistema': rol.es_sistema,
        'activo': rol.activo,
        'deleted_at': rol.deleted_at,
        'permisos': sorted(rol.permisos.values_list('codigo', flat=True)),
    }


def _snapshot_asignacion(asignacion):
    return {
        'cloud_id': str(asignacion.cloud_id),
        'revision': asignacion.revision,
        'usuario_id': asignacion.usuario_id,
        'usuario_username': asignacion.usuario.username,
        'rol_id': asignacion.rol_id,
        'rol_slug': asignacion.rol.slug,
        'sucursal_id': asignacion.sucursal_id,
        'sucursal_codigo': (
            asignacion.sucursal.codigo if asignacion.sucursal_id else None
        ),
        'activo': asignacion.activo,
        'deleted_at': asignacion.deleted_at,
    }


def _validar_permisos(permisos, *, using):
    permisos = list(permisos or [])
    codigos = {permiso.codigo for permiso in permisos}
    desconocidos = codigos - codigos_catalogo()
    if desconocidos:
        raise RBACMutationError(
            f'Codigos de permiso desconocidos: {sorted(desconocidos)}.'
        )
    if any(_db_de(permiso) != using for permiso in permisos):
        raise RBACMutationError('Los permisos pertenecen a otra base de datos.')
    return permisos


def _slug_disponible(negocio, nombre, *, using, excluir_pk=None):
    base = slugify(nombre)[:90] or 'rol'
    slug = base
    consecutivo = 2
    qs = Rol.objects.using(using).filter(negocio=negocio)
    if excluir_pk is not None:
        qs = qs.exclude(pk=excluir_pk)
    while qs.filter(slug=slug).exists():
        slug = f'{base}-{consecutivo}'
        consecutivo += 1
    return slug


def crear_rol(
    *, actor, negocio, nombre, descripcion='', permisos=(),
    canal=Auditoria.Canal.PORTAL_API, correlacion_id=None, using,
):
    if not using or _db_de(negocio) != using:
        raise RBACMutationError('Negocio y escritura deben usar la misma BD.')
    permisos = _validar_permisos(permisos, using=using)
    with transaction.atomic(using=using):
        negocio = (
            type(negocio).objects.using(using).select_for_update().get(pk=negocio.pk)
        )
        rol = Rol(
            negocio=negocio,
            nombre=nombre,
            slug=_slug_disponible(negocio, nombre, using=using),
            descripcion=descripcion or '',
            activo=True,
        )
        rol.full_clean()
        rol.save(using=using)
        rol.permisos.set(permisos)
        registrar_mutacion(
            accion='permisos.rol.creado', actor=actor, entidad=rol,
            antes={}, despues=_snapshot_rol(rol),
            resultado=Auditoria.Resultado.SUCCEEDED, canal=canal,
            tenant=_tenant_auditoria(negocio, using),
            correlacion_id=correlacion_id, using=using,
        )
    return rol


def actualizar_rol(
    *, rol_id, actor, cambios, canal=Auditoria.Canal.PORTAL_API,
    correlacion_id=None, expected_revision=None, using,
):
    permitidos = {'nombre', 'descripcion', 'activo', 'permisos'}
    desconocidos = set(cambios) - permitidos
    if desconocidos:
        raise RBACMutationError(f'Campos de rol no soportados: {sorted(desconocidos)}.')
    with transaction.atomic(using=using):
        rol = (
            Rol.objects.using(using).select_for_update()
            .select_related('negocio').prefetch_related('permisos').get(pk=rol_id)
        )
        if expected_revision is not None and rol.revision != expected_revision:
            raise RBACRevisionConflict(
                f'Revision esperada {expected_revision}; actual {rol.revision}.'
            )
        antes = _snapshot_rol(rol)
        permisos = None
        if 'permisos' in cambios:
            permisos = _validar_permisos(cambios['permisos'], using=using)

        campos = []
        for campo in ('nombre', 'descripcion', 'activo'):
            if campo in cambios and getattr(rol, campo) != cambios[campo]:
                setattr(rol, campo, cambios[campo])
                campos.append(campo)
        if 'activo' in campos:
            rol.deleted_at = None if rol.activo else timezone.now()
            campos.append('deleted_at')
        if campos:
            rol.full_clean()
            rol.save(using=using, update_fields=[*campos, 'fecha_modificacion'])
        if permisos is not None:
            actuales = set(rol.permisos.values_list('pk', flat=True))
            nuevos = {permiso.pk for permiso in permisos}
            if actuales != nuevos:
                rol.permisos.set(permisos)

        asignaciones_revocadas = 0
        if antes['activo'] and not rol.activo:
            instante = rol.deleted_at or timezone.now()
            asignaciones_revocadas = (
                AsignacionRol.objects.using(using)
                .filter(rol=rol, activo=True)
                .update(
                    activo=False,
                    deleted_at=instante,
                    fecha_modificacion=instante,
                    revision=F('revision') + 1,
                )
            )

        rol.refresh_from_db(using=using)
        despues = _snapshot_rol(rol)
        if antes != despues or asignaciones_revocadas:
            accion = 'permisos.rol.actualizado'
            if antes['activo'] != despues['activo']:
                accion = (
                    'permisos.rol.reactivado'
                    if despues['activo'] else 'permisos.rol.revocado'
                )
            registrar_mutacion(
                accion=accion, actor=actor, entidad=rol,
                antes=antes, despues={
                    **despues,
                    'asignaciones_revocadas': asignaciones_revocadas,
                },
                resultado=Auditoria.Resultado.SUCCEEDED, canal=canal,
                tenant=_tenant_auditoria(rol.negocio, using),
                correlacion_id=correlacion_id,
                using=using,
            )
    return rol


def revocar_rol(*, rol_id, actor, canal=Auditoria.Canal.PORTAL_API, using):
    rol = Rol.objects.using(using).get(pk=rol_id)
    if rol.es_sistema:
        raise RBACMutationError('No se puede eliminar un rol de sistema.')
    return actualizar_rol(
        rol_id=rol_id, actor=actor, cambios={'activo': False},
        canal=canal, using=using,
    )


def _validar_terna(usuario, rol, sucursal, *, using):
    if _db_de(usuario) != using or _db_de(rol) != using:
        raise RBACMutationError('Usuario, rol y escritura deben usar la misma BD.')
    if usuario.negocio_id != rol.negocio_id:
        raise RBACMutationError('El usuario pertenece a otro negocio que el rol.')
    if sucursal is not None and (
        _db_de(sucursal) != using or sucursal.negocio_id != rol.negocio_id
    ):
        raise RBACMutationError('La sucursal pertenece a otro negocio o BD.')


def crear_o_reactivar_asignacion(
    *, actor, usuario, rol, sucursal=None,
    canal=Auditoria.Canal.PORTAL_API, correlacion_id=None, using,
    _auditar=True,
):
    _validar_terna(usuario, rol, sucursal, using=using)
    with transaction.atomic(using=using):
        Rol.objects.using(using).select_for_update().get(pk=rol.pk)
        asignacion = (
            AsignacionRol.objects.using(using).select_for_update()
            .filter(usuario=usuario, rol=rol, sucursal=sucursal).first()
        )
        antes = _snapshot_asignacion(asignacion) if asignacion else {}
        if asignacion is None:
            asignacion = AsignacionRol(
                usuario=usuario, rol=rol, sucursal=sucursal, activo=True,
            )
            asignacion.full_clean()
            asignacion.save(using=using)
            accion = 'permisos.asignacion.creada'
            creada = True
        elif not asignacion.activo:
            asignacion.activo = True
            asignacion.deleted_at = None
            asignacion.save(
                using=using,
                update_fields=['activo', 'deleted_at', 'fecha_modificacion'],
            )
            accion = 'permisos.asignacion.reactivada'
            creada = False
        else:
            return asignacion, False

        asignacion = (
            AsignacionRol.objects.using(using).select_related(
                'usuario', 'rol', 'sucursal', 'rol__negocio',
            ).get(pk=asignacion.pk)
        )
        if _auditar:
            registrar_mutacion(
                accion=accion, actor=actor, entidad=asignacion,
                antes=antes, despues=_snapshot_asignacion(asignacion),
                resultado=Auditoria.Resultado.SUCCEEDED, canal=canal,
                tenant=_tenant_auditoria(rol.negocio, using), sucursal=sucursal,
                correlacion_id=correlacion_id, using=using,
            )
    return asignacion, creada


def actualizar_asignacion(
    *, asignacion_id, actor, cambios, canal=Auditoria.Canal.PORTAL_API,
    correlacion_id=None, expected_revision=None, using,
):
    permitidos = {'usuario', 'rol', 'sucursal', 'activo'}
    desconocidos = set(cambios) - permitidos
    if desconocidos:
        raise RBACMutationError(
            f'Campos de asignacion no soportados: {sorted(desconocidos)}.'
        )
    with transaction.atomic(using=using):
        anterior = (
            AsignacionRol.objects.using(using).select_for_update()
            .select_related('usuario', 'rol', 'rol__negocio')
            .get(pk=asignacion_id)
        )
        if expected_revision is not None and anterior.revision != expected_revision:
            raise RBACRevisionConflict(
                f'Revision esperada {expected_revision}; actual {anterior.revision}.'
            )
        antes = _snapshot_asignacion(anterior)
        usuario = cambios.get('usuario', anterior.usuario)
        rol = cambios.get('rol', anterior.rol)
        sucursal = cambios.get('sucursal', anterior.sucursal)
        activo = cambios.get('activo', anterior.activo)
        _validar_terna(usuario, rol, sucursal, using=using)
        identidad_cambio = (
            usuario.pk != anterior.usuario_id
            or rol.pk != anterior.rol_id
            or getattr(sucursal, 'pk', None) != anterior.sucursal_id
        )

        if identidad_cambio:
            # La identidad natural no se edita: se emite un tombstone para la
            # relacion anterior y una identidad nueva para la relacion destino.
            instante = timezone.now()
            anterior.activo = False
            anterior.deleted_at = instante
            anterior.save(
                using=using,
                update_fields=['activo', 'deleted_at', 'fecha_modificacion'],
            )
            destino, _ = crear_o_reactivar_asignacion(
                actor=actor,
                usuario=usuario,
                rol=rol,
                sucursal=sucursal,
                canal=canal,
                correlacion_id=correlacion_id,
                using=using,
                _auditar=False,
            )
            if not activo and destino.activo:
                destino.activo = False
                destino.deleted_at = instante
                destino.save(
                    using=using,
                    update_fields=['activo', 'deleted_at', 'fecha_modificacion'],
                )
            destino = (
                AsignacionRol.objects.using(using).select_related(
                    'usuario', 'rol', 'sucursal', 'rol__negocio',
                ).get(pk=destino.pk)
            )
            registrar_mutacion(
                accion='permisos.asignacion.movida', actor=actor,
                entidad=anterior, antes=antes,
                despues={
                    **_snapshot_asignacion(anterior),
                    'reemplazada_por': _snapshot_asignacion(destino),
                },
                resultado=Auditoria.Resultado.SUCCEEDED, canal=canal,
                tenant=_tenant_auditoria(anterior.rol.negocio, using),
                sucursal=anterior.sucursal,
                correlacion_id=correlacion_id, using=using,
            )
            return destino

        if activo != anterior.activo:
            anterior.activo = activo
            anterior.deleted_at = None if activo else timezone.now()
            anterior.save(
                using=using,
                update_fields=['activo', 'deleted_at', 'fecha_modificacion'],
            )
            accion = (
                'permisos.asignacion.reactivada'
                if activo else 'permisos.asignacion.revocada'
            )
            registrar_mutacion(
                accion=accion, actor=actor, entidad=anterior,
                antes=antes, despues=_snapshot_asignacion(anterior),
                resultado=Auditoria.Resultado.SUCCEEDED, canal=canal,
                tenant=_tenant_auditoria(anterior.rol.negocio, using),
                sucursal=anterior.sucursal,
                correlacion_id=correlacion_id, using=using,
            )
        return anterior


def revocar_asignacion(
    *, asignacion_id, actor, canal=Auditoria.Canal.PORTAL_API, using,
):
    return actualizar_asignacion(
        asignacion_id=asignacion_id, actor=actor,
        cambios={'activo': False}, canal=canal, using=using,
    )
