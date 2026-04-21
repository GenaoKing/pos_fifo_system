"""
apps/api/serializers/sync.py
Serializers para sincronización (sucursal → cloud).

Estos serializers validan los eventos que las sucursales envían al cloud.
Un evento es una acción que ocurrió en la sucursal (venta, cierre de caja,
anulación) y se envía como JSON para ser procesada en el cloud.

TODO: FASE 2 — Estos serializers se activarán cuando exista el modelo
Sucursal y EventoSync.
"""

from rest_framework import serializers


class EventoSyncSerializer(serializers.Serializer):
    """
    Valida un evento individual enviado por una sucursal.
    
    Tipos de evento soportados:
    - VENTA_CREADA: nueva venta procesada
    - VENTA_ANULADA: venta anulada con devolución FIFO
    - CIERRE_CAJA: cierre de turno con arqueo
    - MOVIMIENTO_CAJA: retiro, gasto, ingreso
    
    El payload es un JSONField libre cuya estructura depende del tipo_evento.
    La validación del payload se hace en el view según el tipo.
    """
    TIPOS_EVENTO = [
        'VENTA_CREADA',
        'VENTA_ANULADA',
        'CIERRE_CAJA',
        'MOVIMIENTO_CAJA',
    ]

    tipo_evento = serializers.ChoiceField(choices=[(t, t) for t in TIPOS_EVENTO])
    payload = serializers.JSONField()
    hash_payload = serializers.CharField(
        max_length=64,
        help_text='SHA-256 del payload para deduplicación / idempotencia'
    )
    timestamp = serializers.DateTimeField(
        help_text='Fecha/hora del evento en la sucursal (timezone-aware)'
    )

    def validate_payload(self, value):
        """Validación básica: el payload no puede estar vacío."""
        if not value:
            raise serializers.ValidationError('El payload no puede estar vacío.')
        return value


class EventoBatchSerializer(serializers.Serializer):
    """
    Valida un batch de eventos enviados por una sucursal.
    
    Las sucursales acumulan eventos mientras trabajan offline
    y los envían en batch cuando recuperan conexión.
    
    Uso:
        POST /api/v1/sync/eventos/
        {
            "eventos": [
                { "tipo_evento": "VENTA_CREADA", "payload": {...}, ... },
                { "tipo_evento": "CIERRE_CAJA", "payload": {...}, ... }
            ]
        }
    """
    eventos = EventoSyncSerializer(many=True)

    def validate_eventos(self, value):
        """Limitar tamaño del batch."""
        if len(value) > 100:
            raise serializers.ValidationError(
                f'Máximo 100 eventos por batch. Recibidos: {len(value)}'
            )
        return value


class SyncStatusSerializer(serializers.Serializer):
    """
    Respuesta del estado de sincronización de una sucursal.
    
    Uso:
        GET /api/v1/sync/status/
        → {
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
    sucursal_codigo = serializers.CharField()
    eventos_pendientes = serializers.IntegerField()
    eventos_confirmados = serializers.IntegerField()
    eventos_error = serializers.IntegerField()
    ultima_sync = serializers.DateTimeField(allow_null=True)
    version_maestros = serializers.DictField(
        child=serializers.DateTimeField()
    )