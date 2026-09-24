"""Serializers del panel portal de administración C04 p5.2."""
from django.contrib.auth import get_user_model

from rest_framework import serializers

from apps.configuracion.models import ConfiguracionNegocio
from apps.configuracion.services import CAMPOS_PORTAL_EDITABLES
from apps.permisos.models import Rol
from apps.sucursales.models import Sucursal


Usuario = get_user_model()


class MotivoSerializer(serializers.Serializer):
    motivo = serializers.CharField(max_length=500, trim_whitespace=True)

    def validate_motivo(self, value):
        if not value:
            raise serializers.ValidationError('motivo es obligatorio.')
        return value


class UsuarioAdministracionSerializer(serializers.ModelSerializer):
    """Alta/baja portal sin filtrar password ni alterar identidades inmutables."""

    password = serializers.CharField(
        write_only=True, trim_whitespace=False, required=False, min_length=8,
    )
    rol_asignacion = serializers.PrimaryKeyRelatedField(
        queryset=Rol.objects.all(), write_only=True, required=False,
    )
    sucursal = serializers.PrimaryKeyRelatedField(
        queryset=Sucursal.objects.all(), write_only=True, required=False,
        allow_null=True,
    )
    motivo = serializers.CharField(write_only=True, required=False, max_length=500)

    class Meta:
        model = Usuario
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name', 'rol', 'activo',
            'fecha_creacion', 'fecha_modificacion', 'password', 'rol_asignacion',
            'sucursal', 'motivo',
        ]
        read_only_fields = ['id', 'rol', 'fecha_creacion', 'fecha_modificacion']

    def validate(self, attrs):
        raw = set(self.initial_data.keys())
        if self.instance is None:
            permitidos = {
                'username', 'email', 'password', 'rol_asignacion', 'sucursal',
                'first_name', 'last_name',
            }
            desconocidos = raw - permitidos
            if desconocidos:
                raise serializers.ValidationError({
                    campo: 'Campo no permitido para el alta de usuario.'
                    for campo in sorted(desconocidos)
                })
            requeridos = {'username', 'email', 'password', 'rol_asignacion'}
            faltantes = requeridos - raw
            if faltantes:
                raise serializers.ValidationError({
                    campo: 'Este campo es obligatorio para el alta.'
                    for campo in sorted(faltantes)
                })
            return attrs

        permitidos = {'first_name', 'last_name', 'activo', 'motivo'}
        desconocidos = raw - permitidos
        if desconocidos:
            raise serializers.ValidationError({
                campo: 'Campo inmutable o no soportado por esta ruta.'
                for campo in sorted(desconocidos)
            })
        if not attrs or set(attrs) == {'motivo'}:
            raise serializers.ValidationError('Debe enviar un campo editable.')
        if not (attrs.get('motivo') or '').strip():
            raise serializers.ValidationError({'motivo': 'motivo es obligatorio.'})
        return attrs


class SucursalAdministracionSerializer(serializers.ModelSerializer):
    """Sucursal sin API key ni usuario de servicio en la superficie portal."""

    motivo = serializers.CharField(write_only=True, required=False, max_length=500)

    class Meta:
        model = Sucursal
        fields = [
            'id', 'codigo', 'nombre', 'direccion', 'telefono', 'activa',
            'fecha_creacion', 'fecha_modificacion', 'ultima_sync', 'motivo',
        ]
        read_only_fields = ['id', 'fecha_creacion', 'fecha_modificacion', 'ultima_sync']

    def validate(self, attrs):
        raw = set(self.initial_data.keys())
        if self.instance is None:
            permitidos = {'codigo', 'nombre', 'direccion', 'telefono', 'activa'}
            desconocidos = raw - permitidos
            if desconocidos:
                raise serializers.ValidationError({
                    campo: 'Campo no permitido para el alta de sucursal.'
                    for campo in sorted(desconocidos)
                })
            faltantes = {'codigo', 'nombre'} - raw
            if faltantes:
                raise serializers.ValidationError({
                    campo: 'Este campo es obligatorio para el alta.'
                    for campo in sorted(faltantes)
                })
            return attrs

        permitidos = {'nombre', 'direccion', 'telefono', 'activa', 'motivo'}
        desconocidos = raw - permitidos
        if desconocidos:
            raise serializers.ValidationError({
                campo: 'codigo es inmutable o el campo no esta soportado.'
                for campo in sorted(desconocidos)
            })
        if not attrs or set(attrs) == {'motivo'}:
            raise serializers.ValidationError('Debe enviar un campo editable.')
        if not (attrs.get('motivo') or '').strip():
            raise serializers.ValidationError({'motivo': 'motivo es obligatorio.'})
        return attrs


class ConfiguracionAdministracionSerializer(serializers.ModelSerializer):
    """Allowlist de configuración operativa; módulos y secretos quedan fuera."""

    sucursal_codigo = serializers.CharField(source='sucursal.codigo', read_only=True)
    sucursal_nombre = serializers.CharField(source='sucursal.nombre', read_only=True)
    motivo = serializers.CharField(write_only=True, required=False, max_length=500)

    class Meta:
        model = ConfiguracionNegocio
        fields = [
            'id', 'sucursal', 'sucursal_codigo', 'sucursal_nombre',
            *sorted(CAMPOS_PORTAL_EDITABLES),
            'fecha_creacion', 'fecha_modificacion', 'motivo',
        ]
        read_only_fields = [
            'id', 'sucursal', 'sucursal_codigo', 'sucursal_nombre',
            'fecha_creacion', 'fecha_modificacion',
        ]

    def validate(self, attrs):
        raw = set(self.initial_data.keys())
        permitidos = set(CAMPOS_PORTAL_EDITABLES) | {'motivo'}
        desconocidos = raw - permitidos
        if desconocidos:
            raise serializers.ValidationError({
                campo: 'Campo no permitido por la configuración portal.'
                for campo in sorted(desconocidos)
            })
        if not attrs or set(attrs) == {'motivo'}:
            raise serializers.ValidationError('Debe enviar un campo editable.')
        if not (attrs.get('motivo') or '').strip():
            raise serializers.ValidationError({'motivo': 'motivo es obligatorio.'})
        return attrs
