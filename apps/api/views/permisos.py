"""
apps/api/views/permisos.py
Endpoints de administración de RBAC para el portal (PR2).

Permiten al admin de un negocio configurar sus roles, los permisos de cada rol
y la asignación de roles a usuarios. Todo está:
  - gated por el permiso `permisos.administrar`, y
  - scoped al negocio del solicitante (negocio_actual) → un admin del negocio A
    no puede ver ni tocar los roles/asignaciones del negocio B.

Contrato:
    GET    /api/v1/permisos/catalogo/          catálogo global (read-only)
    GET    /api/v1/permisos/roles/             roles del negocio
    POST   /api/v1/permisos/roles/             crear rol
    PATCH  /api/v1/permisos/roles/<id>/        editar (incl. lista de permisos)
    DELETE /api/v1/permisos/roles/<id>/        borrar (no roles de sistema)
    GET/POST/PATCH/DELETE /api/v1/permisos/asignaciones/
"""
from django.utils.text import slugify
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated

from apps.negocios.utils import negocio_actual
from apps.permisos.models import AsignacionRol, Permiso, Rol

from ..permissions import requiere_permiso
from ..serializers.permisos import (
    AsignacionRolSerializer,
    PermisoSerializer,
    RolSerializer,
)

# Gating común: requiere el meta-permiso de administración.
ADMIN_RBAC = [IsAuthenticated, requiere_permiso('permisos.administrar')]


def _slug_rol_unico(negocio, nombre):
    """Slug único del rol dentro del negocio (unique_together negocio, slug)."""
    base = slugify(nombre)[:90] or 'rol'
    slug = base
    i = 2
    while Rol.objects.filter(negocio=negocio, slug=slug).exists():
        slug = f'{base}-{i}'
        i += 1
    return slug


class PermisoViewSet(viewsets.ReadOnlyModelViewSet):
    """Catálogo global de permisos (read-only). Sin paginar: es pequeño."""
    permission_classes = ADMIN_RBAC
    serializer_class = PermisoSerializer
    pagination_class = None
    queryset = Permiso.objects.all()


class RolViewSet(viewsets.ModelViewSet):
    """CRUD de roles, scoped al negocio del solicitante."""
    permission_classes = ADMIN_RBAC
    serializer_class = RolSerializer
    pagination_class = None

    def get_queryset(self):
        negocio = negocio_actual(self.request)
        if negocio is None:
            return Rol.objects.none()
        return Rol.objects.filter(negocio=negocio).prefetch_related('permisos')

    def perform_create(self, serializer):
        negocio = negocio_actual(self.request)
        if negocio is None:
            raise ValidationError(
                'No se pudo determinar el negocio. SYSADMIN debe pasar ?negocio=<id>.'
            )
        serializer.save(
            negocio=negocio,
            slug=_slug_rol_unico(negocio, serializer.validated_data['nombre']),
        )

    def perform_destroy(self, instance):
        if instance.es_sistema:
            raise PermissionDenied(
                'No se puede eliminar un rol de sistema. Puedes desactivarlo o '
                'editar sus permisos.'
            )
        instance.delete()


class AsignacionRolViewSet(viewsets.ModelViewSet):
    """Asignaciones usuario→rol del negocio (anti escalada cross-tenant)."""
    permission_classes = ADMIN_RBAC
    serializer_class = AsignacionRolSerializer
    pagination_class = None

    def get_queryset(self):
        negocio = negocio_actual(self.request)
        if negocio is None:
            return AsignacionRol.objects.none()
        return (
            AsignacionRol.objects.filter(rol__negocio=negocio)
            .select_related('usuario', 'rol', 'sucursal')
        )

    def perform_create(self, serializer):
        self._validar_tenant(serializer.validated_data)
        serializer.save()

    def perform_update(self, serializer):
        self._validar_tenant(serializer.validated_data)
        serializer.save()

    def _validar_tenant(self, data):
        """El rol/sucursal deben ser del negocio del solicitante; el usuario no
        puede pertenecer a otro negocio."""
        negocio = negocio_actual(self.request)
        if negocio is None:
            raise ValidationError(
                'No se pudo determinar el negocio. SYSADMIN debe pasar ?negocio=<id>.'
            )
        rol = data.get('rol')
        usuario = data.get('usuario')
        sucursal = data.get('sucursal')
        if rol is not None and rol.negocio_id != negocio.id:
            raise ValidationError({'rol': 'El rol no pertenece a tu negocio.'})
        if usuario is not None and getattr(usuario, 'negocio_id', None) not in (None, negocio.id):
            raise ValidationError({'usuario': 'El usuario pertenece a otro negocio.'})
        if sucursal is not None and sucursal.negocio_id != negocio.id:
            raise ValidationError({'sucursal': 'La sucursal no pertenece a tu negocio.'})
