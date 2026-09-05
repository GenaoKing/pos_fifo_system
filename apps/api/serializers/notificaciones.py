from rest_framework import serializers

from apps.notificaciones.models import DestinatarioNotificacion, SuscripcionPush


class NotificacionSerializer(serializers.ModelSerializer):
    tipo = serializers.CharField(source='evento.tipo_evento')
    titulo = serializers.CharField(source='evento.titulo')
    cuerpo = serializers.CharField(source='evento.cuerpo')
    fecha_hecho = serializers.DateTimeField(source='evento.ocurrido_en')
    sucursal = serializers.SerializerMethodField()
    datos = serializers.JSONField(source='evento.datos')
    ruta_detalle = serializers.SerializerMethodField()

    class Meta:
        model = DestinatarioNotificacion
        fields = [
            'id', 'tipo', 'nivel', 'titulo', 'cuerpo', 'fecha_hecho',
            'sucursal', 'datos', 'ruta_detalle', 'leida_en',
        ]

    def get_sucursal(self, obj):
        sucursal = obj.evento.sucursal
        if sucursal is None:
            return None
        return {'id': sucursal.id, 'codigo': sucursal.codigo, 'nombre': sucursal.nombre}

    def get_ruta_detalle(self, obj):
        return obj.evento.ruta or f'/notificaciones/{obj.pk}'


class SuscripcionPushSerializer(serializers.ModelSerializer):
    keys = serializers.DictField(write_only=True, required=False)

    class Meta:
        model = SuscripcionPush
        fields = [
            'id', 'endpoint', 'keys', 'nombre_dispositivo', 'activa',
            'ultimo_exito_en', 'creado_en', 'actualizado_en',
        ]
        read_only_fields = ['activa', 'ultimo_exito_en', 'creado_en', 'actualizado_en']
        extra_kwargs = {'endpoint': {'validators': []}}

    def validate(self, attrs):
        if self.instance is None:
            keys = attrs.get('keys') or {}
            if not keys.get('p256dh') or not keys.get('auth'):
                raise serializers.ValidationError(
                    {'keys': 'La suscripcion debe incluir p256dh y auth.'}
                )
        return attrs

class ReglaNotificacionEntradaSerializer(serializers.Serializer):
    destinatario_tipo = serializers.ChoiceField(choices=('ROL', 'USUARIO'))
    rol = serializers.IntegerField(required=False)
    usuario = serializers.IntegerField(required=False)
    tipo_evento = serializers.CharField(max_length=64)
    activa = serializers.BooleanField(required=False, default=True)
    modo = serializers.ChoiceField(
        choices=('INCLUIR', 'EXCLUIR'), required=False, default='INCLUIR',
    )
    enviar_push = serializers.BooleanField(required=False, default=True)
    parametros = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):
        tipo = attrs['destinatario_tipo']
        if tipo == 'ROL' and not attrs.get('rol'):
            raise serializers.ValidationError({'rol': 'Selecciona un rol.'})
        if tipo == 'USUARIO' and not attrs.get('usuario'):
            raise serializers.ValidationError({'usuario': 'Selecciona un usuario.'})
        return attrs
