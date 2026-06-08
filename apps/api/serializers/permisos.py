"""
apps/api/serializers/permisos.py
Serializers para la administración de RBAC desde el portal (PR2).
"""
from rest_framework import serializers

from apps.permisos.models import AsignacionRol, Permiso, Rol


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
            'id', 'negocio', 'nombre', 'slug', 'descripcion',
            'es_sistema', 'activo', 'permisos',
        ]
        read_only_fields = ['negocio', 'slug', 'es_sistema']


class AsignacionRolSerializer(serializers.ModelSerializer):
    """Asignación usuario→rol (opcionalmente acotada a una sucursal)."""

    usuario_username = serializers.CharField(source='usuario.username', read_only=True)
    rol_nombre = serializers.CharField(source='rol.nombre', read_only=True)

    class Meta:
        model = AsignacionRol
        fields = [
            'id', 'usuario', 'usuario_username', 'rol', 'rol_nombre',
            'sucursal', 'activo',
        ]
