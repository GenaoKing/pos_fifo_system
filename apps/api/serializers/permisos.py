"""
apps/api/serializers/permisos.py
Serializers para la administración de RBAC desde el portal (PR2).
"""
from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.permisos.models import AsignacionRol, Permiso, Rol
from apps.sucursales.models import Sucursal

Usuario = get_user_model()


class PermisoSerializer(serializers.ModelSerializer):
    """Catálogo de permisos (read-only). El frontend agrupa por `modulo`."""

    class Meta:
        model = Permiso
        fields = ['id', 'codigo', 'nombre', 'descripcion', 'modulo']


class RolSerializer(serializers.ModelSerializer):
    """
    Rol del negocio. `permisos` se expone/edita como lista de códigos del
    catálogo (ej. ["clientes.crear", "ventas.anular"]).

    `negocio`, `slug` y `es_sistema` son de solo lectura: el negocio lo fuerza
    el viewset (negocio_actual), el slug se genera del nombre, y los roles de
    sistema se crean por seed (sus permisos sí se pueden editar).
    """
    permisos = serializers.SlugRelatedField(
        slug_field='codigo',
        queryset=Permiso.objects.all(),
        many=True,
        required=False,
    )

    class Meta:
        model = Rol
        fields = [
            'id', 'cloud_id', 'revision', 'negocio', 'nombre', 'slug',
            'descripcion', 'es_sistema', 'activo', 'deleted_at', 'permisos',
        ]
        read_only_fields = [
            'cloud_id', 'revision', 'negocio', 'slug', 'es_sistema', 'deleted_at',
        ]


class AsignacionRolSerializer(serializers.ModelSerializer):
    """Asignación usuario→rol (opcionalmente acotada a una sucursal)."""

    usuario_username = serializers.CharField(source='usuario.username', read_only=True)
    rol_nombre = serializers.CharField(source='rol.nombre', read_only=True)
    sucursal_nombre = serializers.CharField(source='sucursal.nombre', read_only=True)

    class Meta:
        model = AsignacionRol
        fields = [
            'id', 'cloud_id', 'revision', 'usuario', 'usuario_username', 'rol',
            'rol_nombre', 'sucursal', 'sucursal_nombre', 'activo', 'deleted_at',
            'fecha_modificacion',
        ]
        read_only_fields = ['cloud_id', 'revision', 'deleted_at', 'fecha_modificacion']
        # Sin UniqueTogetherValidator automático: el viewset hace
        # reactivate-or-create sobre (usuario, rol, sucursal), conviviendo con el
        # soft-delete (filas inactivas que el validador rechazaría al re-asignar).
        validators = []


class UsuarioAsignableSerializer(serializers.ModelSerializer):
    """Usuario del negocio, para poblar el selector de asignación (read-only).

    `rol` es el campo legacy informativo (SYSADMIN/ADMIN/CAJERA); el enforcement
    real vive en las asignaciones del motor RBAC.
    """

    nombre_completo = serializers.SerializerMethodField()

    class Meta:
        model = Usuario
        fields = ['id', 'username', 'nombre_completo', 'email', 'rol', 'activo']

    def get_nombre_completo(self, obj):
        nombre = f'{obj.first_name} {obj.last_name}'.strip()
        return nombre or obj.username


class SucursalAsignableSerializer(serializers.ModelSerializer):
    """Sucursal del negocio, para acotar opcionalmente una asignación."""

    class Meta:
        model = Sucursal
        fields = ['id', 'codigo', 'nombre']
