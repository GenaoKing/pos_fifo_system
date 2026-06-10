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
from django.contrib.auth import get_user_model
from django.utils.text import slugify
from rest_framework import status, viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.negocios.utils import negocio_actual
from apps.permisos.models import AsignacionRol, Permiso, Rol
from apps.sucursales.models import Sucursal

from ..permissions import requiere_permiso
from ..serializers.permisos import (
    AsignacionRolSerializer,
    PermisoSerializer,
    RolSerializer,
    SucursalAsignableSerializer,
    UsuarioAsignableSerializer,
)

Usuario = get_user_model()

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

    def create(self, request, *args, **kwargs):
        """Crear o **reactivar** una asignación.

        Como `perform_destroy` hace soft-delete (deja la fila con `activo=False`
        para que la baja se pueda propagar por sync), un re-alta de la misma
        terna (usuario, rol, sucursal) chocaría con el `unique_together`. En vez
        de fallar, reactivamos la fila existente (idempotente). El serializer
        tiene el `UniqueTogetherValidator` automático desactivado para permitirlo.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        self._validar_tenant(data)

        existente = AsignacionRol.objects.filter(
            usuario=data['usuario'],
            rol=data['rol'],
            sucursal=data.get('sucursal'),
        ).first()
        if existente is not None:
            existente.activo = True
            existente.save(update_fields=['activo', 'fecha_modificacion'])
            salida = self.get_serializer(existente)
            return Response(salida.data, status=status.HTTP_200_OK)

        serializer.save()
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_update(self, serializer):
        self._validar_tenant(serializer.validated_data)
        serializer.save()

    def perform_destroy(self, instance):
        # Soft-delete: permite propagar la baja por sync incremental. Un delete
        # fisico no deja tombstone ni fecha_modificacion para la sucursal.
        instance.activo = False
        instance.save(update_fields=['activo', 'fecha_modificacion'])

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


class UsuarioAsignableViewSet(viewsets.ReadOnlyModelViewSet):
    """Usuarios del negocio del solicitante, para el selector de asignación.

    Read-only: la gestión de usuarios (alta/baja) vive fuera de RBAC. Aquí solo
    se enumeran para poder asignarles roles. Scoped al negocio (anti cross-tenant).
    """
    permission_classes = ADMIN_RBAC
    serializer_class = UsuarioAsignableSerializer
    pagination_class = None

    def get_queryset(self):
        negocio = negocio_actual(self.request)
        if negocio is None:
            return Usuario.objects.none()
        return Usuario.objects.filter(negocio=negocio).order_by('username')


class SucursalAsignableViewSet(viewsets.ReadOnlyModelViewSet):
    """Sucursales del negocio, para acotar opcionalmente una asignación."""
    permission_classes = ADMIN_RBAC
    serializer_class = SucursalAsignableSerializer
    pagination_class = None

    def get_queryset(self):
        negocio = negocio_actual(self.request)
        if negocio is None:
            return Sucursal.objects.none()
        return Sucursal.objects.filter(negocio=negocio).order_by('codigo')
