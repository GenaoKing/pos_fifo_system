"""Ciclo de vida soportado de usuarios operativos del POS."""
from django.contrib.auth import get_user_model
from django.db import transaction

from apps.auditoria.models import Auditoria
from apps.auditoria.services import registrar_mutacion
from apps.permisos.models import AsignacionRol


class ProvisioningUsuarioError(ValueError):
    pass


def _db_de(obj):
    return getattr(getattr(obj, '_state', None), 'db', None)


def _snapshot(usuario):
    return {
        'username': usuario.username,
        'email': usuario.email,
        'nombre': usuario.get_full_name(),
        'rol_legacy': usuario.rol,
        'activo': usuario.activo,
        'is_staff': usuario.is_staff,
        'is_superuser': usuario.is_superuser,
        'negocio_id': usuario.negocio_id,
        'credential_scope': 'LOCAL_POS',
    }


def _autorizar_gestion(actor, negocio, sucursal, using):
    if actor is None or _db_de(actor) != using:
        raise ProvisioningUsuarioError('El actor debe pertenecer a la BD tenant.')
    if not getattr(actor, 'activo', False):
        raise ProvisioningUsuarioError('El actor esta inactivo.')
    if not getattr(actor, 'is_superuser', False) and actor.negocio_id != negocio.pk:
        raise ProvisioningUsuarioError('El actor pertenece a otro negocio.')
    if not actor.tiene_permiso('permisos.administrar', sucursal=sucursal):
        raise ProvisioningUsuarioError('Sin permiso permisos.administrar.')


def provisionar_usuario(
    *, actor, negocio, username, email, password, rol, sucursal=None,
    canal=Auditoria.Canal.PORTAL_API, correlacion_id=None, using,
):
    """Crea usuario + asignacion RBAC + CT-01 en una transaccion de tenant."""
    if not using or _db_de(negocio) != using or _db_de(rol) != using:
        raise ProvisioningUsuarioError('Negocio, rol y escritura deben usar la misma BD.')
    if rol.negocio_id != negocio.pk:
        raise ProvisioningUsuarioError('El rol pertenece a otro negocio.')
    if sucursal is not None and (
        _db_de(sucursal) != using or sucursal.negocio_id != negocio.pk
    ):
        raise ProvisioningUsuarioError('La sucursal pertenece a otro negocio o BD.')
    _autorizar_gestion(actor, negocio, sucursal, using)

    User = get_user_model()
    with transaction.atomic(using=using):
        if User.objects.using(using).filter(username__iexact=username.strip()).exists():
            raise ProvisioningUsuarioError('El username ya existe en el tenant.')
        if User.objects.using(using).filter(email__iexact=email.strip()).exists():
            raise ProvisioningUsuarioError('El email local ya existe en el tenant.')

        usuario = User.objects.db_manager(using).create_human_user(
            username=username,
            email=email,
            password=password,
            negocio=negocio,
            rol='CAJERA',
            activo=True,
            is_staff=False,
        )
        asignacion = AsignacionRol(
            usuario=usuario,
            rol=rol,
            sucursal=sucursal,
            activo=True,
        )
        asignacion.full_clean()
        asignacion.save(using=using)
        registrar_mutacion(
            accion='usuarios.usuario.provisionado',
            actor=actor,
            entidad=usuario,
            antes={},
            despues={
                **_snapshot(usuario),
                'rol_slug': rol.slug,
                'sucursal_codigo': getattr(sucursal, 'codigo', None),
            },
            resultado=Auditoria.Resultado.SUCCEEDED,
            canal=canal,
            tenant=negocio.slug,
            sucursal=sucursal,
            correlacion_id=correlacion_id,
            metadata={'credential_policy': 'LOCAL_POS_SEPARATE_FROM_PORTAL'},
            using=using,
        )
    return usuario, asignacion


def actualizar_usuario(
    *, usuario_id, actor, cambios, motivo, canal, sucursal=None,
    correlacion_id=None, using,
):
    """Actualiza campos no credenciales bajo lock y deja diff CT-01."""
    permitidos = {'first_name', 'last_name', 'email', 'activo', 'is_staff'}
    desconocidos = set(cambios) - permitidos
    if desconocidos:
        raise ProvisioningUsuarioError(
            f'Campos no soportados por el ciclo de vida: {sorted(desconocidos)}.'
        )
    if not motivo or not motivo.strip():
        raise ProvisioningUsuarioError('motivo es obligatorio.')

    User = get_user_model()
    with transaction.atomic(using=using):
        usuario = (
            User.objects.using(using).select_for_update()
            .select_related('negocio').get(pk=usuario_id)
        )
        _autorizar_gestion(actor, usuario.negocio, sucursal, using)
        antes = _snapshot(usuario)
        for campo, valor in cambios.items():
            setattr(usuario, campo, valor)
        usuario.full_clean()
        usuario.save(using=using, update_fields=[*cambios, 'fecha_modificacion'])
        despues = _snapshot(usuario)
        accion = 'usuarios.usuario.actualizado'
        if antes['activo'] != despues['activo']:
            accion = (
                'usuarios.usuario.activado'
                if despues['activo'] else 'usuarios.usuario.desactivado'
            )
        registrar_mutacion(
            accion=accion,
            actor=actor,
            entidad=usuario,
            antes=antes,
            despues=despues,
            resultado=Auditoria.Resultado.SUCCEEDED,
            canal=canal,
            tenant=usuario.negocio.slug if usuario.negocio_id else None,
            correlacion_id=correlacion_id,
            metadata={
                'motivo': motivo.strip(),
                'credential_policy': 'LOCAL_POS_SEPARATE_FROM_PORTAL',
            },
            using=using,
        )
    return usuario
