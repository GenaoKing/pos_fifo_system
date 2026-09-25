"""Superficie portal A06 para conflictos de maestros CT-04."""
from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime

from django.db import transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.negocios.utils import resolver_negocio
from apps.sync.master_conflicts import (
    ResolucionConflictoError,
    resolver_conflicto_maestro as resolver_conflicto_maestro_service,
)
from apps.sync.models import MutacionMaestro
from apps.tenancy.context import get_current_tenant_alias


SCHEMA_LISTA = 'master.conflict-list.v1'
SCHEMA_ITEM = 'master.conflict.v1'
SCHEMA_RESOLUCION = 'master.conflict-resolution.v1'
_ESTADOS = frozenset((
    MutacionMaestro.Estado.CONFLICTO,
    MutacionMaestro.Estado.RECHAZADA,
))
_ENTIDADES = frozenset((
    MutacionMaestro.Entidad.PRODUCTO,
    MutacionMaestro.Entidad.CATEGORIA,
))
_ACCIONES = frozenset((
    'CONSERVAR_CLOUD',
    'APLICAR_LOCAL',
))


def _permiso_lectura(entidad):
    return {
        MutacionMaestro.Entidad.PRODUCTO: 'productos.ver',
        MutacionMaestro.Entidad.CATEGORIA: 'categorias.ver',
    }[entidad]


def _cursor_encode(mutacion):
    contenido = json.dumps({
        'creado_at': mutacion.creado_at.isoformat(),
        'id': mutacion.pk,
    }, separators=(',', ':')).encode('utf-8')
    return base64.urlsafe_b64encode(contenido).decode('ascii').rstrip('=')


def _cursor_decode(valor):
    if not valor:
        return None
    try:
        padded = valor + '=' * (-len(valor) % 4)
        datos = json.loads(base64.urlsafe_b64decode(padded).decode('utf-8'))
        creado_at = datetime.fromisoformat(str(datos['creado_at']).replace('Z', '+00:00'))
        identificador = int(datos['id'])
        if creado_at.tzinfo is None or identificador < 1:
            raise ValueError
        return creado_at, identificador
    except (
        KeyError, TypeError, ValueError, UnicodeDecodeError,
        binascii.Error, json.JSONDecodeError,
    ):
        raise ValueError('cursor inválido')


def _page_size(request):
    valor = request.query_params.get('page_size', '50')
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        raise ValueError('page_size inválido')
    if not 1 <= numero <= 100:
        raise ValueError('page_size debe estar entre 1 y 100')
    return numero


def _display_snapshot(mutacion):
    delta = mutacion.delta or {}
    if mutacion.entidad == MutacionMaestro.Entidad.PRODUCTO:
        sku = (delta.get('sku') or {}).get('after')
        nombre = (delta.get('nombre') or {}).get('after')
        if sku and nombre:
            return f'{sku} — {nombre}'
        if nombre:
            return str(nombre)
        if sku:
            return str(sku)
        return f'Producto local #{mutacion.entidad_id}'
    nombre = (delta.get('nombre') or {}).get('after')
    return str(nombre or f'Categoría local #{mutacion.entidad_id}')


def _acciones(mutacion, *, resuelta=False):
    if resuelta or mutacion.cloud_entidad_id is None:
        return []
    return ['CONSERVAR_CLOUD', 'APLICAR_LOCAL']


def serializar_conflicto(mutacion, *, resuelta=False):
    """Forma congelada ``master.conflict.v1`` consumida por C04."""
    detalle = (
        mutacion.conflicto_detalle
        if mutacion.estado == MutacionMaestro.Estado.CONFLICTO
        else mutacion.ultimo_error
    )
    return {
        'schema_version': SCHEMA_ITEM,
        'mutacion_id': str(mutacion.mutacion_id),
        'estado': mutacion.estado,
        'codigo': mutacion.codigo_resultado,
        'detalle': detalle,
        'entidad': {
            'tipo': mutacion.entidad,
            'local_id': mutacion.entidad_id,
            'cloud_id': mutacion.cloud_entidad_id,
            'display_snapshot': _display_snapshot(mutacion),
        },
        'sucursal': {'codigo': mutacion.sucursal_codigo},
        'actor': {'username_snapshot': mutacion.actor_username},
        'operacion': mutacion.operacion,
        'revision': {
            'base': mutacion.revision_base,
            'cloud_actual': mutacion.cloud_revision,
        },
        'delta': mutacion.delta or {},
        'creado_at': mutacion.creado_at.isoformat(),
        'conflicto_at': (
            mutacion.conflicto_at.isoformat() if mutacion.conflicto_at else None
        ),
        'bloquea_nuevas_ventas': (
            mutacion.estado == MutacionMaestro.Estado.CONFLICTO
        ),
        'acciones': _acciones(mutacion, resuelta=resuelta),
    }


def _respuesta_error(mensaje, *, status_code=status.HTTP_400_BAD_REQUEST, codigo=''):
    cuerpo = {'detail': mensaje}
    if codigo:
        cuerpo['code'] = codigo
    return Response(cuerpo, status=status_code)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def conflictos_maestros(request):
    """Lista propuestas negativas visibles para el principal portal actual."""
    resolucion = resolver_negocio(request)
    if not resolucion.permitido:
        return _respuesta_error(
            resolucion.motivo or 'Sin acceso.',
            status_code=status.HTTP_403_FORBIDDEN,
        )
    estado_filtro = request.query_params.get('estado')
    if estado_filtro and estado_filtro not in _ESTADOS:
        return _respuesta_error('estado inválido')
    entidad_filtro = request.query_params.get('entidad')
    if entidad_filtro and entidad_filtro not in _ENTIDADES:
        return _respuesta_error('entidad inválida')
    try:
        cursor = _cursor_decode(request.query_params.get('cursor'))
        page_size = _page_size(request)
    except ValueError as exc:
        return _respuesta_error(str(exc))

    using = get_current_tenant_alias() or 'default'
    queryset = resolucion.filtrar(
        MutacionMaestro.objects.using(using).filter(
            estado__in=_ESTADOS,
            resolucion_conflicto__isnull=True,
        ).select_related('sucursal', 'sucursal__negocio'),
        campo='sucursal__negocio',
    )
    if estado_filtro:
        queryset = queryset.filter(estado=estado_filtro)
    if entidad_filtro:
        queryset = queryset.filter(entidad=entidad_filtro)
    if sucursal_codigo := request.query_params.get('sucursal_codigo'):
        queryset = queryset.filter(sucursal_codigo=sucursal_codigo)
    if cursor:
        creado_at, identificador = cursor
        queryset = queryset.filter(
            Q(creado_at__gt=creado_at)
            | Q(creado_at=creado_at, id__gt=identificador)
        )

    visibles = []
    hay_mas = False
    for mutacion in queryset.order_by('creado_at', 'id').iterator(chunk_size=200):
        if not request.user.tiene_permiso(
            _permiso_lectura(mutacion.entidad), sucursal=mutacion.sucursal,
        ):
            continue
        if len(visibles) == page_size:
            hay_mas = True
            break
        visibles.append(mutacion)

    return Response({
        'schema_version': SCHEMA_LISTA,
        'next_cursor': _cursor_encode(visibles[-1]) if hay_mas else None,
        'items': [serializar_conflicto(item) for item in visibles],
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def resolver_conflicto_maestro(request, mutacion_id):
    """Aplica una decisión humana con CAS para un conflicto CT-04."""
    datos = request.data if isinstance(request.data, dict) else {}
    if datos.get('schema_version') != SCHEMA_RESOLUCION:
        return _respuesta_error(
            'schema_version debe ser master.conflict-resolution.v1',
            codigo='MASTER_CONFLICT_SCHEMA_INVALID',
        )
    accion = datos.get('accion')
    if accion not in _ACCIONES:
        return _respuesta_error('accion inválida')
    motivo = str(datos.get('motivo') or '').strip()
    if not 1 <= len(motivo) <= 500:
        return _respuesta_error('motivo debe tener entre 1 y 500 caracteres')
    cloud_revision_observada = str(datos.get('cloud_revision_observada') or '').strip()
    if not cloud_revision_observada:
        return _respuesta_error('cloud_revision_observada es obligatoria')

    resolucion = resolver_negocio(request)
    if not resolucion.permitido:
        return _respuesta_error(
            resolucion.motivo or 'Sin acceso.',
            status_code=status.HTTP_403_FORBIDDEN,
        )
    using = get_current_tenant_alias() or 'default'
    with transaction.atomic(using=using):
        queryset = resolucion.filtrar(
            MutacionMaestro.objects.using(using).select_for_update().select_related(
                'sucursal', 'sucursal__negocio',
            ),
            campo='sucursal__negocio',
        )
        try:
            mutacion = queryset.get(mutacion_id=mutacion_id)
        except MutacionMaestro.DoesNotExist:
            return _respuesta_error(
                'La propuesta no existe o no pertenece a su alcance.',
                status_code=status.HTTP_404_NOT_FOUND,
            )
        try:
            resultado = resolver_conflicto_maestro_service(
                ledger=mutacion,
                actor=request.user,
                accion=accion,
                motivo=motivo,
                cloud_revision_observada=cloud_revision_observada,
                using=using,
            )
        except ResolucionConflictoError as exc:
            return _respuesta_error(
                str(exc), status_code=exc.status_code, codigo=exc.codigo,
            )

        return Response(
            serializar_conflicto(resultado.mutacion, resuelta=resultado.resuelta),
            status=(status.HTTP_200_OK if resultado.resuelta else status.HTTP_409_CONFLICT),
        )
