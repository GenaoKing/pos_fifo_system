"""Ciclo de vida soportado de usuarios operativos del POS."""
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from apps.auditoria.models import Auditoria
from apps.auditoria.services import registrar_mutacion
from apps.permisos.models import AsignacionRol


class ProvisioningUsuarioError(ValueError):
    pass


class ProvisioningPortalUsuarioError(ProvisioningUsuarioError):
    """Fallo estable del ciclo de vida que une tenant e identidad cloud."""

    pass


def _db_de(obj):
    return getattr(getattr(obj, '_state', None), 'db', None)


def _tenant_auditoria(negocio, using):
    """El tenant técnico prevalece sobre el slug de negocio en DB-per-tenant."""
    if using.startswith('tnt_'):
        from apps.tenancy.context import get_current_tenant_key

        return get_current_tenant_key()
    return negocio.slug if negocio is not None else None


def _snapshot(usuario, *, credential_scope='LOCAL_POS'):
    return {
        'username': usuario.username,
        'email': usuario.email,
        'nombre': usuario.get_full_name(),
        'rol_legacy': usuario.rol,
        'activo': usuario.activo,
        'is_staff': usuario.is_staff,
        'is_superuser': usuario.is_superuser,
        'negocio_id': usuario.negocio_id,
        'credential_scope': (
            'PORTAL_IDENTITY_AND_LOCAL_POS'
            if credential_scope == 'PORTAL_IDENTITY_AND_LOCAL_POS'
            else 'LOCAL_POS'
        ),
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
    canal=Auditoria.Canal.PORTAL_API, correlacion_id=None,
    credential_scope='LOCAL_POS_SEPARATE_FROM_PORTAL', first_name='', last_name='', using,
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
            first_name=(first_name or '').strip(),
            last_name=(last_name or '').strip(),
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
            tenant=_tenant_auditoria(negocio, using),
            sucursal=sucursal,
            correlacion_id=correlacion_id,
            metadata={'credential_policy': credential_scope},
            using=using,
        )
    return usuario, asignacion


def actualizar_usuario(
    *, usuario_id, actor, cambios, motivo, canal, sucursal=None,
    correlacion_id=None, credential_scope='LOCAL_POS', using,
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
        # `negocio` es nullable en instalaciones legacy. PostgreSQL no permite
        # FOR UPDATE sobre el lado nullable de ese LEFT OUTER JOIN; bloquear
        # primero solo la fila de usuario conserva la exclusión mutua y luego
        # resuelve su negocio en una lectura normal.
        usuario = User.objects.using(using).select_for_update().get(pk=usuario_id)
        _autorizar_gestion(actor, usuario.negocio, sucursal, using)
        antes = _snapshot(usuario, credential_scope=credential_scope)
        for campo, valor in cambios.items():
            setattr(usuario, campo, valor)
        usuario.full_clean()
        usuario.save(using=using, update_fields=[*cambios, 'fecha_modificacion'])
        despues = _snapshot(usuario, credential_scope=credential_scope)
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
            tenant=_tenant_auditoria(usuario.negocio, using),
            correlacion_id=correlacion_id,
            metadata={
                'motivo': motivo.strip(),
                'credential_policy': credential_scope,
            },
            using=using,
        )
    return usuario


def provisionar_usuario_portal(
    *, actor, negocio, username, email, password, rol, nombre='', first_name='',
    last_name='', tenant=None, sucursal=None,
    correlacion_id=None, using,
):
    """Alta portal de un usuario y, en cloud, de su acceso autenticable.

    La identidad y la membership viven en ``default`` y el usuario/auditoria en
    la BD del tenant. Django no ofrece una transaccion distribuida entre ambas;
    por eso el control plane se mantiene abierto hasta que termina el alta
    tenant y, si su commit falla, se revoca compensatoriamente el usuario local.
    Nunca se deja una cuenta local activa como resultado de una alta portal que
    no pudo confirmar su membership.
    """
    if tenant is None:
        return provisionar_usuario(
            actor=actor,
            negocio=negocio,
            username=username,
            email=email,
            password=password,
            rol=rol,
            sucursal=sucursal,
            correlacion_id=correlacion_id,
            credential_scope='LOCAL_POS_SEPARATE_FROM_PORTAL',
            first_name=first_name,
            last_name=last_name,
            using=using,
        )

    if _db_de(tenant) != 'default':
        raise ProvisioningPortalUsuarioError(
            'El tenant de la membership debe pertenecer al control plane.'
        )

    from apps.tenancy.models import Identity, Membership

    usuario = None
    email_normalizado = (email or '').strip().lower()
    try:
        # El atomic externo cubre Identity+Membership. `provisionar_usuario`
        # abre el atomic de la base tenant que corresponde a su dominio.
        with transaction.atomic(using='default'):
            if Identity.objects.using('default').filter(
                email__iexact=email_normalizado
            ).exists():
                raise ProvisioningPortalUsuarioError(
                    'Ya existe una Identity para este email; no se puede reutilizar '
                    'en el alta portal.'
                )

            identity = Identity(
                email=email_normalizado,
                nombre=(nombre or '').strip()[:200],
                activo=True,
                is_global=False,
            )
            identity.set_password(password)
            identity.full_clean()
            identity.save(using='default')

            membership = Membership(
                identity=identity,
                tenant=tenant,
                username=username.strip(),
                rol='CAJERA',
                activo=True,
            )
            membership.full_clean()
            membership.save(using='default')

            usuario, asignacion = provisionar_usuario(
                actor=actor,
                negocio=negocio,
                username=username,
                email=email_normalizado,
                password=password,
                rol=rol,
                sucursal=sucursal,
                correlacion_id=correlacion_id,
                credential_scope='PORTAL_IDENTITY_AND_LOCAL_POS',
                first_name=first_name,
                last_name=last_name,
                using=using,
            )
    except IntegrityError as exc:
        if usuario is not None:
            _compensar_estado_local(usuario.pk, activo=False, using=using)
        # La prevalidacion cubre el caso normal; esta rama es la carrera entre
        # dos altas simultaneas y no debe filtrar el constraint de la BD.
        raise ProvisioningPortalUsuarioError(
            'No se pudo confirmar el alta: username o Identity ya existe.'
        ) from exc
    except Exception:
        if usuario is not None:
            _compensar_estado_local(usuario.pk, activo=False, using=using)
        raise
    return usuario, asignacion


def actualizar_usuario_portal(
    *, usuario_id, actor, cambios, motivo, tenant=None, sucursal=None,
    correlacion_id=None, using,
):
    """Actualiza un usuario y revoca/reactiva su Membership cuando corresponde.

    ``activo=False`` no es un flag cosmético: la autenticacion tenant revalida
    la membership en cada request. La baja es por tanto efectiva para access y
    refresh JWT sin borrar ni la cuenta local ni la evidencia de auditoria.
    """
    if tenant is None or 'activo' not in cambios:
        return actualizar_usuario(
            usuario_id=usuario_id,
            actor=actor,
            cambios=cambios,
            motivo=motivo,
            canal=Auditoria.Canal.PORTAL_API,
            sucursal=sucursal,
            correlacion_id=correlacion_id,
            credential_scope=(
                'PORTAL_IDENTITY_AND_LOCAL_POS'
                if tenant is not None else 'LOCAL_POS_SEPARATE_FROM_PORTAL'
            ),
            using=using,
        )

    if _db_de(tenant) != 'default':
        raise ProvisioningPortalUsuarioError(
            'El tenant de la membership debe pertenecer al control plane.'
        )

    from apps.tenancy.models import Membership

    User = get_user_model()
    usuario_previo = (
        User.objects.using(using).select_related('negocio').filter(pk=usuario_id).first()
    )
    if usuario_previo is None:
        raise ProvisioningPortalUsuarioError('Usuario inexistente.')
    _autorizar_gestion(actor, usuario_previo.negocio, sucursal, using)

    activo_anterior = bool(usuario_previo.activo)
    activo_deseado = bool(cambios['activo'])
    usuario = None
    try:
        with transaction.atomic(using='default'):
            membership = (
                Membership.objects.using('default')
                .select_for_update()
                .select_related('identity')
                .filter(tenant=tenant, username=usuario_previo.username)
                .first()
            )
            if membership is None:
                raise ProvisioningPortalUsuarioError(
                    'El usuario no tiene Membership portal en este tenant.'
                )
            if activo_deseado and not membership.identity.activo:
                raise ProvisioningPortalUsuarioError(
                    'No se puede reactivar: la Identity global esta inactiva.'
                )

            # Idempotencia de la baja: no agrega una segunda mutacion si los
            # dos lados ya estaban exactamente revocados.
            if (
                activo_anterior == activo_deseado
                and bool(membership.activo) == activo_deseado
                and set(cambios) == {'activo'}
            ):
                return usuario_previo

            membership.activo = activo_deseado
            membership.save(using='default', update_fields=['activo', 'fecha_modificacion'])
            usuario = actualizar_usuario(
                usuario_id=usuario_id,
                actor=actor,
                cambios=cambios,
                motivo=motivo,
                canal=Auditoria.Canal.PORTAL_API,
                sucursal=sucursal,
                correlacion_id=correlacion_id,
                credential_scope='PORTAL_IDENTITY_AND_LOCAL_POS',
                using=using,
            )
    except Exception:
        # Si el commit del control plane fallo DESPUES de confirmar el atomic
        # tenant, devolver el usuario a su estado previo evita una mitad activa
        # o revocada distinta de la membership. Esta es compensacion, no una
        # promesa falsa de atomicidad distribuida.
        if usuario is not None:
            _compensar_estado_local(usuario.pk, activo=activo_anterior, using=using)
        raise
    return usuario


def _compensar_estado_local(usuario_id, *, activo, using):
    """Compensacion fail-closed de una operacion multi-DB incompleta.

    No intenta emitir un CT-01 exitoso: el alta/baja principal fallo. Se limita
    a restaurar el flag local dentro de una transaccion corta para que la
    credencial operativa no quede en un estado de acceso inesperado.
    """
    User = get_user_model()
    with transaction.atomic(using=using):
        usuario = User.objects.using(using).select_for_update().filter(pk=usuario_id).first()
        if usuario is None or bool(usuario.activo) == bool(activo):
            return
        usuario.activo = bool(activo)
        usuario.save(using=using, update_fields=['activo', 'fecha_modificacion'])
