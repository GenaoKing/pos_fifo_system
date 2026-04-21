"""
apps/api/views/sync.py
Endpoints de sincronización (sucursal → cloud).

Estos endpoints reciben eventos de las sucursales y reportan
el estado de sincronización.

TODO: FASE 2 — Requiere modelos Sucursal y EventoSync.
La lógica está completa, solo falta descomentar los imports
y queries que dependen de esos modelos.
"""

import hashlib
import json
import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from ..permissions import EsSucursalAutenticada
from ..serializers.sync import EventoBatchSerializer, SyncStatusSerializer

logger = logging.getLogger('pos_system')


@api_view(['POST'])
@permission_classes([EsSucursalAutenticada])
@throttle_classes([ScopedRateThrottle])
def recibir_eventos(request):
    """
    POST /api/v1/sync/eventos/
    
    Recibe un batch de eventos desde una sucursal.
    
    Body:
        {
            "eventos": [
                {
                    "tipo_evento": "VENTA_CREADA",
                    "payload": { ... datos completos de la venta ... },
                    "hash_payload": "sha256...",
                    "timestamp": "2026-04-17T14:30:00-04:00"
                },
                ...
            ]
        }
    
    Response (éxito):
        {
            "recibidos": 5,
            "duplicados": 1,
            "errores": 0,
            "detalle": [
                { "hash": "abc...", "estado": "CONFIRMADO" },
                { "hash": "def...", "estado": "DUPLICADO" },
                ...
            ]
        }
    
    Idempotencia:
        El hash_payload se usa para detectar duplicados. Si un evento
        con el mismo hash ya fue procesado, se marca como DUPLICADO
        sin error — esto permite que la sucursal reenvíe sin miedo.
    """
    serializer = EventoBatchSerializer(data=request.data)

    if not serializer.is_valid():
        return Response(
            {'error': 'Datos inválidos', 'detalle': serializer.errors},
            status=status.HTTP_400_BAD_REQUEST
        )

    eventos = serializer.validated_data['eventos']
    resultados = []
    recibidos = 0
    duplicados = 0
    errores = 0

    for evento_data in eventos:
        hash_payload = evento_data['hash_payload']

        try:
            # TODO: FASE 2 — Descomentar cuando existan los modelos
            # ──────────────────────────────────────────────────────
            # from apps.sync.models import EventoSync
            #
            # # Verificar duplicado por hash
            # if EventoSync.objects.filter(hash_payload=hash_payload).exists():
            #     duplicados += 1
            #     resultados.append({
            #         'hash': hash_payload,
            #         'estado': 'DUPLICADO'
            #     })
            #     continue
            #
            # # Crear evento
            # EventoSync.objects.create(
            #     sucursal=request.auth.sucursal,
            #     tipo_evento=evento_data['tipo_evento'],
            #     payload=evento_data['payload'],
            #     hash_payload=hash_payload,
            #     estado='CONFIRMADO',
            #     confirmed_at=timezone.now(),
            # )
            # ──────────────────────────────────────────────────────

            # Placeholder hasta Fase 2: log del evento
            logger.info(
                f"[SYNC] Evento recibido: {evento_data['tipo_evento']} "
                f"hash={hash_payload[:12]}... "
                f"usuario={request.user.username}"
            )

            recibidos += 1
            resultados.append({
                'hash': hash_payload,
                'estado': 'CONFIRMADO'
            })

        except Exception as e:
            errores += 1
            resultados.append({
                'hash': hash_payload,
                'estado': 'ERROR',
                'error': str(e)
            })
            logger.error(
                f"[SYNC] Error procesando evento {hash_payload[:12]}: {e}"
            )

    return Response({
        'recibidos': recibidos,
        'duplicados': duplicados,
        'errores': errores,
        'detalle': resultados,
        'timestamp': timezone.now(),
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([EsSucursalAutenticada])
def sync_status(request):
    """
    GET /api/v1/sync/status/
    
    Retorna el estado de sincronización de la sucursal autenticada.
    
    Response:
        {
            "sucursal_codigo": "SD-001",
            "eventos_pendientes": 0,
            "eventos_confirmados": 342,
            "eventos_error": 2,
            "ultima_sync": "2026-04-17T14:30:00-04:00",
            "version_maestros": {
                "productos": "2026-04-17T10:00:00-04:00",
                "categorias": "2026-04-15T08:00:00-04:00",
                "clientes": "2026-04-16T12:00:00-04:00"
            }
        }
    """
    # TODO: FASE 2 — Descomentar cuando existan los modelos
    # ──────────────────────────────────────────────────────
    # from apps.sync.models import EventoSync, VersionMaestro
    # sucursal = request.auth.sucursal
    #
    # pendientes = EventoSync.objects.filter(
    #     sucursal=sucursal, estado='PENDIENTE'
    # ).count()
    # confirmados = EventoSync.objects.filter(
    #     sucursal=sucursal, estado='CONFIRMADO'
    # ).count()
    # con_error = EventoSync.objects.filter(
    #     sucursal=sucursal, estado='ERROR'
    # ).count()
    #
    # ultima = EventoSync.objects.filter(
    #     sucursal=sucursal, estado='CONFIRMADO'
    # ).order_by('-confirmed_at').values_list('confirmed_at', flat=True).first()
    #
    # versiones = {}
    # for vm in VersionMaestro.objects.filter(sucursal=sucursal):
    #     versiones[vm.tabla] = vm.version
    #
    # data = {
    #     'sucursal_codigo': sucursal.codigo,
    #     'eventos_pendientes': pendientes,
    #     'eventos_confirmados': confirmados,
    #     'eventos_error': con_error,
    #     'ultima_sync': ultima,
    #     'version_maestros': versiones,
    # }
    # ──────────────────────────────────────────────────────

    # Placeholder hasta Fase 2
    data = {
        'sucursal_codigo': 'LOCAL',
        'eventos_pendientes': 0,
        'eventos_confirmados': 0,
        'eventos_error': 0,
        'ultima_sync': None,
        'version_maestros': {
            'productos': None,
            'categorias': None,
            'clientes': None,
        },
        'mensaje': 'Sync engine pendiente — requiere Fase 2 (modelo Sucursal)',
    }

    return Response(data)