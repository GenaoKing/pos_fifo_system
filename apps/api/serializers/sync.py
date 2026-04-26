"""
apps/api/serializers/sync.py
Serializers para sincronizacion (sucursal -> cloud).

Estos serializers validan los eventos que las sucursales envian al cloud.
La lista de tipos validos se importa de apps.sync.constants para mantener
una sola fuente de verdad (evita desincronizacion con EventoSync.TIPO_CHOICES).
"""
from rest_framework import serializers

from apps.sync.constants import TIPOS_EVENTO_CODIGOS


class EventoSyncSerializer(serializers.Serializer):
    """Valida un evento individual enviado por una sucursal."""

    tipo_evento = serializers.ChoiceField(
        choices=[(t, t) for t in TIPOS_EVENTO_CODIGOS]
    )
    payload = serializers.JSONField()
    hash_payload = serializers.CharField(
        max_length=64,
        help_text='SHA-256 del payload para deduplicacion / idempotencia'
    )
    timestamp = serializers.DateTimeField(
        help_text='Fecha/hora del evento en la sucursal (timezone-aware)'
    )

    def validate_payload(self, value):
        if not value:
            raise serializers.ValidationError('El payload no puede estar vacio.')
        return value


class EventoBatchSerializer(serializers.Serializer):
    """Valida un batch de eventos enviados por una sucursal."""

    eventos = EventoSyncSerializer(many=True)

    def validate_eventos(self, value):
        if len(value) > 100:
            raise serializers.ValidationError(
                f'Maximo 100 eventos por batch. Recibidos: {len(value)}'
            )
        return value


class SyncStatusSerializer(serializers.Serializer):
    """Respuesta del estado de sincronizacion de una sucursal."""

    sucursal_codigo = serializers.CharField()
    eventos_pendientes = serializers.IntegerField()
    eventos_confirmados = serializers.IntegerField()
    eventos_error = serializers.IntegerField()
    ultima_sync = serializers.DateTimeField(allow_null=True)
    version_maestros = serializers.DictField(
        child=serializers.DateTimeField()
    )