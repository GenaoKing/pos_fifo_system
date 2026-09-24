"""Escrituras tenant-scoped del panel C04 p5.2."""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.configuracion.models import ConfiguracionNegocio
from apps.configuracion.services import (
    ConfiguracionMutationError,
    actualizar_configuracion,
)
from apps.negocios.utils import negocio_actual
from apps.sucursales.models import Sucursal
from apps.sucursales.services import (
    SucursalMutationError,
    actualizar_sucursal,
    crear_sucursal,
    desactivar_sucursal,
)
from apps.tenancy.context import tenancy_enabled
from apps.tenancy.models import Tenant
from apps.usuarios.services import (
    ProvisioningUsuarioError,
    actualizar_usuario_portal,
    provisionar_usuario_portal,
)

from ..permissions import requiere_permiso
from ..serializers.administracion import (
    ConfiguracionAdministracionSerializer,
    MotivoSerializer,
    SucursalAdministracionSerializer,
    UsuarioAdministracionSerializer,
)


Usuario = get_user_model()
ADMIN_IDENTIDAD = [IsAuthenticated, requiere_permiso('permisos.administrar')]
ADMIN_CONFIGURACION = [IsAuthenticated, requiere_permiso('configuracion.administrar')]


def _error_de_dominio(exc):
    """No deja que un ``full_clean()`` de dominio se convierta en HTTP 500."""
    if isinstance(exc, DjangoValidationError):
        return getattr(exc, 'message_dict', None) or exc.messages
    return str(exc)


class _TenantScopedMixin:
    """Nunca traduce un tenant ausente a un queryset global."""

    def _negocio(self):
        negocio = negocio_actual(self.request)
        if negocio is None:
            raise ValidationError(
                'No se pudo determinar el negocio. SYSADMIN debe pasar ?negocio=<id>.'
            )
        return negocio

    def _tenant_control_plane(self):
        if not tenancy_enabled():
            return None
        tenant_key = getattr(self.request.user, 'tenant_key', None)
        if not tenant_key:
            raise ValidationError(
                'La escritura cloud exige un JWT tenant-scoped; use impersonacion.'
            )
        tenant = Tenant.objects.using('default').filter(
            tenant_key=tenant_key,
            activo=True,
        ).first()
        if tenant is None:
            raise ValidationError('El tenant de la sesion no existe o esta inactivo.')
        return tenant


class UsuarioAdministracionViewSet(_TenantScopedMixin, viewsets.ModelViewSet):
    permission_classes = ADMIN_IDENTIDAD
    serializer_class = UsuarioAdministracionSerializer
    pagination_class = None
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        negocio = negocio_actual(self.request)
        if negocio is None:
            return Usuario.objects.none()
        return Usuario.objects.filter(negocio=negocio).order_by('username')

    def create(self, request, *args, **kwargs):
        negocio = self._negocio()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        rol = data.pop('rol_asignacion')
        sucursal = data.pop('sucursal', None)
        password = data.pop('password')
        nombre = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
        try:
            usuario, _asignacion = provisionar_usuario_portal(
                actor=request.user,
                negocio=negocio,
                rol=rol,
                sucursal=sucursal,
                password=password,
                nombre=nombre,
                tenant=self._tenant_control_plane(),
                using=negocio._state.db,
                **data,
            )
        except (ProvisioningUsuarioError, DjangoValidationError) as exc:
            raise ValidationError(_error_de_dominio(exc))
        return Response(self.get_serializer(usuario).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        motivo = data.pop('motivo')
        try:
            usuario = actualizar_usuario_portal(
                usuario_id=instance.pk,
                actor=request.user,
                cambios=data,
                motivo=motivo,
                tenant=self._tenant_control_plane(),
                using=instance._state.db,
            )
        except (ProvisioningUsuarioError, DjangoValidationError) as exc:
            raise ValidationError(_error_de_dominio(exc))
        return Response(self.get_serializer(usuario).data)

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        motivo = MotivoSerializer(data=request.data)
        motivo.is_valid(raise_exception=True)
        try:
            actualizar_usuario_portal(
                usuario_id=instance.pk,
                actor=request.user,
                cambios={'activo': False},
                motivo=motivo.validated_data['motivo'],
                tenant=self._tenant_control_plane(),
                using=instance._state.db,
            )
        except (ProvisioningUsuarioError, DjangoValidationError) as exc:
            raise ValidationError(_error_de_dominio(exc))
        return Response(status=status.HTTP_204_NO_CONTENT)


class SucursalAdministracionViewSet(_TenantScopedMixin, viewsets.ModelViewSet):
    permission_classes = ADMIN_IDENTIDAD
    serializer_class = SucursalAdministracionSerializer
    pagination_class = None
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        negocio = negocio_actual(self.request)
        if negocio is None:
            return Sucursal.objects.none()
        return Sucursal.objects.filter(negocio=negocio).order_by('codigo')

    def create(self, request, *args, **kwargs):
        negocio = self._negocio()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        try:
            sucursal = crear_sucursal(
                actor=request.user,
                negocio=negocio,
                cambios=data,
                using=negocio._state.db,
            )
        except (SucursalMutationError, DjangoValidationError) as exc:
            raise ValidationError(_error_de_dominio(exc))
        return Response(self.get_serializer(sucursal).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        motivo = data.pop('motivo')
        try:
            sucursal = actualizar_sucursal(
                sucursal_id=instance.pk,
                actor=request.user,
                cambios=data,
                motivo=motivo,
                using=instance._state.db,
            )
        except (SucursalMutationError, DjangoValidationError) as exc:
            raise ValidationError(_error_de_dominio(exc))
        return Response(self.get_serializer(sucursal).data)

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        motivo = MotivoSerializer(data=request.data)
        motivo.is_valid(raise_exception=True)
        try:
            desactivar_sucursal(
                sucursal_id=instance.pk,
                actor=request.user,
                motivo=motivo.validated_data['motivo'],
                using=instance._state.db,
            )
        except (SucursalMutationError, DjangoValidationError) as exc:
            raise ValidationError(_error_de_dominio(exc))
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConfiguracionAdministracionViewSet(
    _TenantScopedMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = ADMIN_CONFIGURACION
    serializer_class = ConfiguracionAdministracionSerializer
    pagination_class = None
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_queryset(self):
        negocio = negocio_actual(self.request)
        if negocio is None:
            return ConfiguracionNegocio.objects.none()
        return (
            ConfiguracionNegocio.objects.filter(sucursal__negocio=negocio)
            .select_related('sucursal')
            .order_by('sucursal__codigo')
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        motivo = data.pop('motivo')
        try:
            configuracion = actualizar_configuracion(
                configuracion_id=instance.pk,
                actor=request.user,
                cambios=data,
                motivo=motivo,
                using=instance._state.db,
            )
        except (ConfiguracionMutationError, DjangoValidationError) as exc:
            raise ValidationError(_error_de_dominio(exc))
        return Response(self.get_serializer(configuracion).data)
