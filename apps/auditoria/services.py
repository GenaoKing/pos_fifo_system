"""Contrato transaccional y redactado ``audit.event.v1`` (CT-01)."""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import connections
from django.db.models import Model

from apps.tenancy.context import (
    get_current_tenant_alias,
    get_current_tenant_key,
)


SCHEMA_VERSION = 'audit.event.v1'
_REF_NAMESPACE = uuid.UUID('ff367539-61bb-4e17-9907-41d38ed2df09')
_SENSITIVE_KEY = re.compile(
    r'(?:pass(?:word)?|pwd|token|secret|authorization|cookie|api[_-]?key|'
    r'private[_-]?key|client[_-]?secret|vapid|db[_-]?(?:password|pass))',
    re.IGNORECASE,
)
_INLINE_SECRET = re.compile(
    r'(?i)\b(password|passwd|pwd|token|secret|authorization|cookie|api[_-]?key|'
    r'private[_-]?key|client[_-]?secret|vapid(?:_private_key)?)'
    r'(\s*[:=]\s*)([^\s,;\]}]+)',
)
_BEARER = re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+')
_URL_PASSWORD = re.compile(r'(?i)([a-z][a-z0-9+.-]*://[^:/\s]+:)([^@/\s]+)(@)')
_MAX_TEXT = 2048


class AuditContractError(RuntimeError):
    """Fallo estable del productor CT-01, sin copiar payload sensible."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(f'{code}: {message}')


def _redactar_texto(value):
    value = _BEARER.sub('Bearer [REDACTED]', str(value))
    value = _URL_PASSWORD.sub(r'\1[REDACTED]\3', value)
    value = _INLINE_SECRET.sub(r'\1\2[REDACTED]', value)
    if len(value) > _MAX_TEXT:
        value = value[:_MAX_TEXT] + '…[TRUNCATED]'
    return value


def redactar_payload(value, *, _depth=0):
    """Devuelve solo primitivas JSON y redacta secretos recursivamente."""
    if _depth > 20:
        raise AuditContractError(
            'AUDIT_REDACTION_FAILED', 'El payload excede la profundidad permitida.',
        )
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redactar_texto(value)
    if isinstance(value, BaseException):
        return _redactar_texto(value)
    if isinstance(value, (Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            key = str(key)
            safe[key] = (
                '[REDACTED]'
                if _SENSITIVE_KEY.search(key)
                else redactar_payload(item, _depth=_depth + 1)
            )
        return safe
    if isinstance(value, (list, tuple, set)):
        return [redactar_payload(item, _depth=_depth + 1) for item in value]

    raise AuditContractError(
        'AUDIT_REDACTION_FAILED',
        f'Payload no serializable ({type(value).__name__}).',
    )


def _ref(*parts):
    material = '|'.join(str(part) for part in parts)
    return uuid.uuid5(_REF_NAMESPACE, material)


def _model_identity(obj, *, tenant_key, role):
    if not isinstance(obj, Model) or obj.pk is None:
        raise AuditContractError(
            'AUDIT_IDENTITY_UNRESOLVED', f'{role} no tiene una identidad persistida.',
        )
    return _ref(tenant_key or 'control-plane', obj._meta.label_lower, obj.pk)


def _tenant_key(value):
    if value is None:
        return ''
    for attr in ('tenant_key', 'slug'):
        candidate = getattr(value, attr, None)
        if candidate:
            return str(candidate)
    return str(value).strip()


def _derivar_sucursal(entidad, sucursal):
    if sucursal is not None:
        return sucursal
    from .models import Auditoria
    return Auditoria.derivar_sucursal(entidad)


def _derivar_tenant(entidad, sucursal, tenant):
    explicito = _tenant_key(tenant)
    contexto = get_current_tenant_key() or ''
    if explicito and contexto and explicito != contexto:
        raise AuditContractError(
            'AUDIT_CONTEXT_INVALID', 'El tenant explicito no coincide con el contexto.',
        )
    if contexto:
        return contexto
    if explicito:
        return explicito

    for candidate in (
        getattr(sucursal, 'negocio', None),
        getattr(entidad, 'negocio', None),
        getattr(getattr(entidad, 'sucursal', None), 'negocio', None),
    ):
        key = _tenant_key(candidate)
        if key:
            return key
    return ''


def _actor_data(actor, *, tenant_key, using):
    if actor is None:
        return {
            'usuario': None,
            'ref': _ref('system'),
            'kind': 'SYSTEM',
            'username': '',
            'display': 'Sistema',
            'impersonator_ref': None,
        }

    identity = getattr(actor, 'identity', None)
    base = identity if isinstance(identity, Model) else actor
    ref = _model_identity(base, tenant_key=tenant_key, role='actor')
    username = getattr(actor, 'username', '') or getattr(base, 'email', '') or ''
    display = (
        actor.get_full_name() if hasattr(actor, 'get_full_name') else ''
    ) or username
    is_service = bool(
        getattr(actor, 'is_service_account', False)
        or str(username).lower().startswith(('svc_', 'sync_', 'sucursal_service_'))
    )
    impersonator_ref = None
    if getattr(actor, 'es_impersonado', False) and getattr(actor, 'identity_id', None):
        impersonator_ref = _ref('control-plane', 'tenancy.identity', actor.identity_id)

    actor_meta = getattr(actor, '_meta', None)
    usuario = (
        actor
        if getattr(actor_meta, 'label_lower', '') == 'usuarios.usuario'
        else None
    )
    if usuario is not None and usuario._state.db != using:
        raise AuditContractError(
            'AUDIT_CONTEXT_INVALID', 'El actor operativo pertenece a otra BD.',
        )
    actor_negocio = getattr(usuario, 'negocio', None) if usuario is not None else None
    actor_tenant = _tenant_key(actor_negocio)
    actor_global = bool(
        getattr(actor, 'is_superuser', False)
        or getattr(actor, 'is_global_identity', False)
        or getattr(base, 'is_global', False)
    )
    if actor_tenant and tenant_key and actor_tenant != tenant_key and not actor_global:
        raise AuditContractError(
            'AUDIT_CONTEXT_INVALID', 'El actor pertenece a otro tenant.',
        )
    return {
        'usuario': usuario,
        'ref': ref,
        'kind': 'SERVICE' if is_service else 'USER',
        'username': str(username),
        'display': str(display),
        'impersonator_ref': impersonator_ref,
    }


def registrar_mutacion(
    *, accion, actor, entidad, antes, despues, resultado, canal,
    tenant=None, sucursal=None, correlacion_id=None, idempotencia_key=None,
    metadata=None, error=None, using,
):
    """Persiste un evento CT-01 en la misma BD/transaccion que el dominio."""
    from .models import Auditoria

    if not using or using not in connections:
        raise AuditContractError(
            'AUDIT_CONTEXT_INVALID', 'using es obligatorio y debe existir.',
        )
    connection = connections[using]
    if resultado not in Auditoria.Resultado.values:
        raise AuditContractError('AUDIT_CONTEXT_INVALID', 'resultado no valido.')
    if canal not in set(Auditoria.Canal.values) - {Auditoria.Canal.LEGACY}:
        raise AuditContractError('AUDIT_CONTEXT_INVALID', 'canal no valido.')
    if not Auditoria.ACCION_V1_RE.fullmatch(str(accion or '')):
        raise AuditContractError(
            'AUDIT_CONTEXT_INVALID', 'accion debe ser un codigo namespaced estable.',
        )
    if resultado == Auditoria.Resultado.SUCCEEDED and not connection.in_atomic_block:
        raise AuditContractError(
            'AUDIT_CONTEXT_INVALID',
            'Una mutacion exitosa debe auditarse dentro de transaction.atomic(using).',
        )
    if not isinstance(entidad, Model) or entidad.pk is None:
        raise AuditContractError(
            'AUDIT_IDENTITY_UNRESOLVED', 'La entidad debe estar persistida.',
        )
    if entidad._state.db != using:
        raise AuditContractError(
            'AUDIT_CONTEXT_INVALID', 'La entidad y el evento pertenecen a BDs distintas.',
        )

    alias_contexto = get_current_tenant_alias()
    if using.startswith('tnt_') and alias_contexto != using:
        raise AuditContractError(
            'AUDIT_CONTEXT_INVALID', 'La BD tenant no coincide con el contexto activo.',
        )

    sucursal = _derivar_sucursal(entidad, sucursal)
    tenant_key = _derivar_tenant(entidad, sucursal, tenant)
    if using.startswith('tnt_') and not tenant_key:
        raise AuditContractError(
            'AUDIT_CONTEXT_INVALID', 'Un evento tenant exige tenant_key.',
        )
    if sucursal is not None and sucursal._state.db != using:
        raise AuditContractError(
            'AUDIT_CONTEXT_INVALID', 'La sucursal pertenece a otra BD.',
        )

    actor_data = _actor_data(actor, tenant_key=tenant_key, using=using)
    entity_ref = _model_identity(entidad, tenant_key=tenant_key, role='entidad')
    branch_ref = (
        _model_identity(sucursal, tenant_key=tenant_key, role='sucursal')
        if sucursal is not None else None
    )
    error = redactar_payload(error) if error else None
    if error is not None and not isinstance(error, dict):
        error = {'code': type(error).__name__, 'message': str(error)}
    error_code = str((error or {}).get('code') or '')[:100]
    error_message = str((error or {}).get('message') or '')
    if resultado == Auditoria.Resultado.SUCCEEDED and error_message:
        raise AuditContractError(
            'AUDIT_CONTEXT_INVALID', 'SUCCEEDED no admite error.',
        )

    before_safe = redactar_payload(antes if antes is not None else {})
    after_safe = redactar_payload(despues if despues is not None else {})
    metadata_safe = redactar_payload(metadata if metadata is not None else {})
    json.dumps((before_safe, after_safe, metadata_safe, error), ensure_ascii=False)

    content_type = ContentType.objects.db_manager(using).get_for_model(entidad)
    object_id = entidad.pk if isinstance(entidad.pk, int) and entidad.pk >= 0 else None
    evento = Auditoria(
        schema_version=SCHEMA_VERSION,
        actor_ref=actor_data['ref'],
        actor_kind=actor_data['kind'],
        actor_username=actor_data['username'][:150],
        actor_nombre=actor_data['display'][:300],
        impersonator_ref=actor_data['impersonator_ref'],
        usuario=actor_data['usuario'],
        tenant_key=tenant_key,
        branch_ref=branch_ref,
        branch_code=str(getattr(sucursal, 'codigo', '') or '')[:40],
        sucursal=sucursal,
        canal=canal,
        accion=str(accion),
        descripcion=f'{accion}: {entidad}'[:2048],
        content_type=content_type,
        object_id=object_id,
        entity_type=entidad._meta.label,
        entity_ref=entity_ref,
        entity_display=str(entidad)[:300],
        datos_anteriores=before_safe,
        datos_nuevos=after_safe,
        resultado=resultado,
        exito=resultado == Auditoria.Resultado.SUCCEEDED,
        correlacion_id=correlacion_id,
        idempotencia_key=str(idempotencia_key or '')[:200],
        metadata=metadata_safe,
        error_codigo=error_code,
        mensaje_error=error_message,
    )
    try:
        evento.save(using=using)
    except AuditContractError:
        raise
    except Exception as exc:
        raise AuditContractError(
            'AUDIT_WRITE_FAILED', f'No se pudo persistir el evento ({type(exc).__name__}).',
        ) from exc
    return evento


def evento_transportable(evento):
    """Serializa una fila CT-01 sin depender de FK vivas."""
    error = None
    if evento.error_codigo or evento.mensaje_error:
        error = {'code': evento.error_codigo, 'message': evento.mensaje_error}
    return {
        'schema_version': evento.schema_version,
        'event_id': str(evento.event_id),
        'occurred_at': evento.fecha_hora.isoformat(),
        'recorded_at': evento.registrado_en.isoformat(),
        'actor': {
            'ref': str(evento.actor_ref) if evento.actor_ref else None,
            'kind': evento.actor_kind,
            'username_snapshot': evento.actor_username,
            'display_snapshot': evento.actor_nombre,
            'impersonator_ref': (
                str(evento.impersonator_ref) if evento.impersonator_ref else None
            ),
        },
        'tenant': {'key': evento.tenant_key} if evento.tenant_key else None,
        'branch': ({
            'ref': str(evento.branch_ref),
            'code_snapshot': evento.branch_code,
        } if evento.branch_ref else None),
        'channel': evento.canal,
        'action': evento.accion,
        'entity': {
            'type': evento.entity_type,
            'ref': str(evento.entity_ref) if evento.entity_ref else None,
            'display_snapshot': evento.entity_display,
        },
        'before': evento.datos_anteriores or {},
        'after': evento.datos_nuevos or {},
        'result': evento.resultado,
        'correlation': {
            'correlation_id': (
                str(evento.correlacion_id) if evento.correlacion_id else None
            ),
            'idempotency_key': evento.idempotencia_key or None,
        },
        'metadata': evento.metadata or {},
        'error': error,
    }
