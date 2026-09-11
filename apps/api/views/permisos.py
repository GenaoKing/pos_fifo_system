"""Endpoints tenant-scoped de administracion RBAC (CT-02)."""
from django.contrib.auth import get_user_model
from rest_framework import status, viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.negocios.utils import negocio_actual
from apps.permisos.models import AsignacionRol, Permiso, Rol
from apps.permisos.services import (
    RBACMutationError,
    RBACRevisionConflict,
    actualizar_asignacion,
    actualizar_rol,
    crear_o_reactivar_asignacion,
    crear_rol,
    revocar_asignacion,
    revocar_rol,
)
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
ADMIN_RBAC = [IsAuthenticated, requiere_permiso('permisos.administrar')]


def _revision_esperada(request):
    """Revision optimista opt-in; clientes legacy pueden omitir el header."""
    valor = request.headers.get('X-RBAC-Revision')
    if valor in (None, ''):
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        raise ValidationError({
            'code': 'rbac_revision_invalid',
            'detail': 'X-RBAC-Revision debe ser un entero.',
        })


def _conflicto_revision(exc):
    return Response(
        {'code': 'rbac_revision_conflict', 'detail': str(exc)},
        status=status.HTTP_409_CONFLICT,
    )


class PermisoViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = ADMIN_RBAC
    serializer_class = PermisoSerializer
    pagination_class = None
    queryset = Permiso.objects.all()


class RolViewSet(viewsets.ModelViewSet):
    permission_classes = ADMIN_RBAC
    serializer_class = RolSerializer
    pagination_class = None

    def get_queryset(self):
        negocio = negocio_actual(self.request)
        if negocio is None:
            return Rol.objects.none()
        return Rol.objects.filter(negocio=negocio).prefetch_related('permisos')

    def create(self, request, *args, **kwargs):
        negocio = negocio_actual(request)
        if negocio is None:
            raise ValidationError(
                'No se pudo determinar el negocio. SYSADMIN debe pasar ?negocio=<id>.'
            )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        permisos = data.pop('permisos', [])
        try:
            rol = crear_rol(
                actor=request.user,
                negocio=negocio,
                permisos=permisos,
                using=negocio._state.db,
                **data,
            )
        except RBACMutationError as exc:
            raise ValidationError(str(exc))
        return Response(self.get_serializer(rol).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        try:
            rol = actualizar_rol(
                rol_id=instance.pk,
                actor=request.user,
                cambios=dict(serializer.validated_data),
                expected_revision=_revision_esperada(request),
                using=instance._state.db,
            )
        except RBACRevisionConflict as exc:
            return _conflicto_revision(exc)
        except RBACMutationError as exc:
            raise ValidationError(str(exc))
        return Response(self.get_serializer(rol).data)

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    def perform_destroy(self, instance):
        try:
            revocar_rol(
                rol_id=instance.pk,
                actor=self.request.user,
                using=instance._state.db,
            )
        except RBACMutationError as exc:
            raise PermissionDenied(str(exc))


class AsignacionRolViewSet(viewsets.ModelViewSet):
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
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        self._validar_tenant(data)
        try:
            asignacion, cambio = crear_o_reactivar_asignacion(
                actor=request.user,
                usuario=data['usuario'],
                rol=data['rol'],
                sucursal=data.get('sucursal'),
                using=data['rol']._state.db,
            )
        except RBACMutationError as exc:
            raise ValidationError(str(exc))
        codigo = status.HTTP_201_CREATED if cambio else status.HTTP_200_OK
        return Response(self.get_serializer(asignacion).data, status=codigo)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self._validar_tenant(serializer.validated_data, instance=instance)
        try:
            asignacion = actualizar_asignacion(
                asignacion_id=instance.pk,
                actor=request.user,
                cambios=dict(serializer.validated_data),
                expected_revision=_revision_esperada(request),
                using=instance._state.db,
            )
        except RBACRevisionConflict as exc:
            return _conflicto_revision(exc)
        except RBACMutationError as exc:
            raise ValidationError(str(exc))
        return Response(self.get_serializer(asignacion).data)

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    def perform_destroy(self, instance):
        revocar_asignacion(
            asignacion_id=instance.pk,
            actor=self.request.user,
            using=instance._state.db,
        )

    def _validar_tenant(self, data, instance=None):
        negocio = negocio_actual(self.request)
        if negocio is None:
            raise ValidationError(
                'No se pudo determinar el negocio. SYSADMIN debe pasar ?negocio=<id>.'
            )
        rol = data.get('rol', getattr(instance, 'rol', None))
        usuario = data.get('usuario', getattr(instance, 'usuario', None))
        sucursal = data.get('sucursal', getattr(instance, 'sucursal', None))
        if rol is not None and rol.negocio_id != negocio.id:
            raise ValidationError({'rol': 'El rol no pertenece a tu negocio.'})
        if usuario is not None and getattr(usuario, 'negocio_id', None) != negocio.id:
            raise ValidationError({'usuario': 'El usuario pertenece a otro negocio.'})
        if sucursal is not None and sucursal.negocio_id != negocio.id:
            raise ValidationError({'sucursal': 'La sucursal no pertenece a tu negocio.'})


class UsuarioAsignableViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = ADMIN_RBAC
    serializer_class = UsuarioAsignableSerializer
    pagination_class = None

    def get_queryset(self):
        negocio = negocio_actual(self.request)
        if negocio is None:
            return Usuario.objects.none()
        return Usuario.objects.filter(negocio=negocio).order_by('username')


class SucursalAsignableViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = ADMIN_RBAC
    serializer_class = SucursalAsignableSerializer
    pagination_class = None

    def get_queryset(self):
        negocio = negocio_actual(self.request)
        if negocio is None:
            return Sucursal.objects.none()
        return Sucursal.objects.filter(negocio=negocio).order_by('codigo')
