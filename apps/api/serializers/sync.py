"""
apps/api/serializers/sync.py
Serializers para sincronizacion (sucursal -> cloud).

Estos serializers validan los eventos que las sucursales envian al cloud.
La lista de tipos validos se importa de apps.sync.constants para mantener
una sola fuente de verdad (evita desincronizacion con EventoSync.TIPO_CHOICES).
"""
from rest_framework import serializers

from apps.sync.constants import TIPOS_EVENTO_CODIGOS
from apps.sync.models import MutacionMaestro


class EventoSyncSerializer(serializers.Serializer):
    """Valida un evento individual enviado por una sucursal."""

    event_id = serializers.UUIDField(
        required=False,
        help_text='Identidad estable A04; opcional para POS legacy.',
    )
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


class SondaEventoSerializer(serializers.Serializer):
    """Evento local CONFIRMADO que se contrasta sin mutar el cloud."""

    event_id = serializers.UUIDField(required=False)
    tipo_evento = serializers.ChoiceField(
        choices=[(t, t) for t in TIPOS_EVENTO_CODIGOS]
    )
    payload = serializers.JSONField()
    hash_payload = serializers.CharField(max_length=64)
    objeto_referencia = serializers.CharField(
        max_length=64, required=False, allow_blank=True,
    )

    def validate_payload(self, value):
        if not value:
            raise serializers.ValidationError('El payload no puede estar vacio.')
        return value


class SondaEventoBatchSerializer(serializers.Serializer):
    schema_version = serializers.ChoiceField(
        choices=['sync.reconciliation.v1']
    )
    eventos = SondaEventoSerializer(many=True)

    def validate_eventos(self, value):
        if len(value) > 100:
            raise serializers.ValidationError(
                f'Maximo 100 eventos por sonda. Recibidos: {len(value)}'
            )
        return value


class MutacionMaestroEntradaSerializer(serializers.Serializer):
    """Propuesta A05.3 desde el POS, deliberadamente separada de eventos.

    El serializer valida la envoltura y la forma del delta; la validación de
    dominio y el CAS ocurren en la transacción del receptor, donde ya conocemos
    al actor y la sucursal autenticada del tenant cloud.
    """

    CAMPOS = {
        MutacionMaestro.Entidad.CATEGORIA: {
            'id', 'nombre', 'descripcion', 'activa', 'tipo_negocio',
            'atributos_configurados',
        },
        MutacionMaestro.Entidad.PRODUCTO: {
            'id', 'sku', 'codigo_barras', 'nombre', 'descripcion',
            'categoria_id', 'precio_venta', 'stock_minimo', 'activo',
            'atributos', 'estado', 'marca',
        },
    }

    schema_version = serializers.ChoiceField(choices=['master.mutation.v1'])
    mutacion_id = serializers.UUIDField()
    entidad = serializers.ChoiceField(choices=MutacionMaestro.Entidad.choices)
    entidad_local_id = serializers.IntegerField(min_value=1)
    operacion = serializers.ChoiceField(choices=MutacionMaestro.Operacion.choices)
    revision_base = serializers.CharField(max_length=64, allow_blank=True)
    delta = serializers.JSONField()
    actor_username = serializers.CharField(max_length=150, trim_whitespace=True)
    cloud_entidad_id = serializers.IntegerField(
        required=False, allow_null=True, min_value=1,
    )
    categoria_cloud_id = serializers.IntegerField(
        required=False, allow_null=True, min_value=1,
    )

    def validate(self, attrs):
        entidad = attrs['entidad']
        operacion = attrs['operacion']
        delta = attrs['delta']
        actor_username = attrs['actor_username'].strip().casefold()
        if not actor_username:
            raise serializers.ValidationError({
                'actor_username': 'La propuesta debe identificar al actor local.',
            })
        attrs['actor_username'] = actor_username

        if not isinstance(delta, dict) or not delta:
            raise serializers.ValidationError({
                'delta': 'El delta debe ser un objeto no vacío.',
            })
        desconocidos = set(delta) - self.CAMPOS[entidad]
        if desconocidos:
            raise serializers.ValidationError({
                'delta': f'Campos no mutables: {", ".join(sorted(desconocidos))}.',
            })
        for campo, cambio in delta.items():
            if (
                not isinstance(cambio, dict)
                or set(cambio) != {'before', 'after'}
            ):
                raise serializers.ValidationError({
                    'delta': f'{campo} debe declarar exactamente before y after.',
                })
        identificador = delta.get('id')
        if identificador and identificador.get('after') != attrs['entidad_local_id']:
            raise serializers.ValidationError({
                'delta': 'El id del delta no coincide con entidad_local_id.',
            })

        es_creacion = operacion == MutacionMaestro.Operacion.CREAR
        cloud_entidad_id = attrs.get('cloud_entidad_id')
        if es_creacion:
            if attrs['revision_base'] or cloud_entidad_id is not None:
                raise serializers.ValidationError(
                    'Una creación no puede traer identidad ni revisión cloud.'
                )
        elif not attrs['revision_base'] or cloud_entidad_id is None:
            raise serializers.ValidationError(
                'Una mutación existente exige cloud_entidad_id y revision_base.'
            )

        campo_estado = 'activa' if entidad == MutacionMaestro.Entidad.CATEGORIA else 'activo'
        if operacion in (
            MutacionMaestro.Operacion.ACTIVAR,
            MutacionMaestro.Operacion.DESACTIVAR,
        ):
            if set(delta) - {'id', campo_estado} or campo_estado not in delta:
                raise serializers.ValidationError({
                    'delta': 'Un cambio de estado no puede alterar otros campos.',
                })
            esperado = operacion == MutacionMaestro.Operacion.ACTIVAR
            if delta[campo_estado]['after'] is not esperado:
                raise serializers.ValidationError({
                    'delta': 'La operación no coincide con el estado solicitado.',
                })

        requiere_categoria = (
            entidad == MutacionMaestro.Entidad.PRODUCTO
            and (es_creacion or 'categoria_id' in delta)
        )
        if requiere_categoria and attrs.get('categoria_cloud_id') is None:
            raise serializers.ValidationError({
                'categoria_cloud_id': 'El producto requiere categoría cloud resuelta.',
            })
        if entidad != MutacionMaestro.Entidad.PRODUCTO and 'categoria_cloud_id' in attrs:
            raise serializers.ValidationError({
                'categoria_cloud_id': 'Solo aplica a productos.',
            })
        return attrs


class MutacionMaestroBatchSerializer(serializers.Serializer):
    schema_version = serializers.ChoiceField(choices=['master.mutation.v1'])
    mutaciones = MutacionMaestroEntradaSerializer(many=True)

    def validate_mutaciones(self, value):
        if not value:
            raise serializers.ValidationError('Se requiere al menos una propuesta.')
        if len(value) > 20:
            raise serializers.ValidationError('Máximo 20 propuestas por envío.')
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
