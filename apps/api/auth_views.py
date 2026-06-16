from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from apps.tenancy.context import tenant_context, tenancy_enabled
from apps.tenancy.models import Identity, Membership, Tenant


class LegacyPortalTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['username'] = user.username
        token['rol'] = getattr(user, 'rol', None)
        token['full_name'] = (
            user.get_full_name()
            if hasattr(user, 'get_full_name')
            else user.username
        )
        token['tenant_id'] = user.negocio.slug if getattr(user, 'negocio_id', None) else None
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        _validar_usuario_portal(self.user)
        _touch_user(self.user)
        data['user'] = _user_payload(self.user)
        return data


class TenantPortalLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False)

    def validate(self, attrs):
        email = attrs['email'].strip().lower()
        password = attrs['password']

        identity = Identity.objects.using('default').filter(email__iexact=email).first()
        if identity is None or not identity.check_password(password):
            raise serializers.ValidationError({'detail': 'Credenciales invalidas.'})
        if not identity.activo:
            raise serializers.ValidationError({'detail': 'Identity inactiva.'})

        memberships = list(
            Membership.objects.using('default')
            .select_related('tenant')
            .filter(identity=identity, activo=True, tenant__activo=True)
        )

        if identity.is_global and not memberships:
            return _global_token_payload(identity)

        if len(memberships) != 1:
            raise serializers.ValidationError(
                {'detail': 'El MVP requiere exactamente un tenant activo para login.'}
            )

        membership = memberships[0]
        tenant = membership.tenant
        with tenant_context(tenant):
            User = get_user_model()
            user = User.objects.filter(username=membership.username).first()
            if user is None:
                raise serializers.ValidationError(
                    {'detail': 'Usuario operativo no existe en la base del tenant.'}
                )
            _validar_usuario_portal(user)
            _touch_user(user)
            payload = _tenant_token_payload(identity, tenant, user)

        identity.ultimo_acceso = timezone.now()
        identity.save(update_fields=['ultimo_acceso'])
        return payload


class PortalTokenObtainPairView(APIView):
    permission_classes = []
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        serializer_class = (
            TenantPortalLoginSerializer
            if tenancy_enabled()
            else LegacyPortalTokenObtainPairSerializer
        )
        serializer = serializer_class(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def impersonar_tenant(request):
    """SYSADMIN global -> JWT scoped to one tenant."""
    if not getattr(request.user, 'is_global_identity', False):
        return Response({'detail': 'Solo una Identity global puede impersonar.'}, status=403)

    tenant_key = (request.data.get('tenant_key') or request.data.get('tenant') or '').strip()
    username = (request.data.get('username') or '').strip()
    if not tenant_key:
        return Response({'detail': 'tenant_key es requerido.'}, status=400)

    tenant = Tenant.objects.using('default').filter(tenant_key=tenant_key, activo=True).first()
    if tenant is None:
        return Response({'detail': 'Tenant inactivo o inexistente.'}, status=404)

    with tenant_context(tenant):
        User = get_user_model()
        qs = User.objects.filter(activo=True)
        if username:
            user = qs.filter(username=username).first()
        else:
            user = qs.filter(rol__in=('ADMIN', 'SYSADMIN')).order_by('id').first()
        if user is None:
            return Response({'detail': 'No hay usuario operativo activo para impersonar.'}, status=400)
        payload = _tenant_token_payload(request.user.identity, tenant, user)

    return Response(payload)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def perfil_actual(request):
    return Response(_user_payload(request.user))


def _tenant_token_payload(identity, tenant, user):
    user.tenant_key = tenant.tenant_key
    user.identity_id = identity.pk
    refresh = RefreshToken.for_user(user)
    _add_tenant_claims(refresh, identity, tenant, user)
    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
        'user': _user_payload(user),
    }


def _global_token_payload(identity):
    refresh = RefreshToken()
    refresh['identity_id'] = identity.pk
    refresh['is_global'] = True
    refresh['email'] = identity.email
    refresh['rol'] = 'SYSADMIN'
    identity.ultimo_acceso = timezone.now()
    identity.save(update_fields=['ultimo_acceso'])
    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
        'user': {
            'id': identity.pk,
            'username': identity.email,
            'email': identity.email,
            'full_name': identity.nombre or identity.email,
            'rol': 'SYSADMIN',
            'negocio': None,
            'tenant_id': None,
            'permisos': [],
            'modulos': [],
            'is_global': True,
        },
    }


def _add_tenant_claims(token, identity, tenant, user):
    token['identity_id'] = identity.pk
    token['tenant_key'] = tenant.tenant_key
    token['tenant_id'] = tenant.tenant_key
    token['username'] = user.username
    token['rol'] = getattr(user, 'rol', None)
    token['full_name'] = (
        user.get_full_name()
        if hasattr(user, 'get_full_name')
        else user.username
    )


def _validar_usuario_portal(user):
    if not getattr(user, 'activo', True):
        raise serializers.ValidationError(
            {'detail': 'Usuario inactivo. Contacte al administrador.'},
            code='usuario_inactivo',
        )

    rol = getattr(user, 'rol', None)
    if rol not in ('SYSADMIN', 'ADMIN'):
        raise serializers.ValidationError(
            {'detail': 'Solo administradores pueden acceder al portal cloud.'},
            code='rol_no_autorizado',
        )


def _touch_user(user):
    if hasattr(user, 'ultimo_acceso'):
        user.ultimo_acceso = timezone.now()
        user.save(update_fields=['ultimo_acceso'])


def _user_payload(user):
    if getattr(user, 'is_global_identity', False):
        return {
            'id': user.pk,
            'username': user.username,
            'email': user.email,
            'first_name': '',
            'last_name': '',
            'full_name': user.get_full_name(),
            'rol': 'SYSADMIN',
            'negocio': None,
            'tenant_id': None,
            'permisos': [],
            'modulos': [],
            'is_global': True,
        }

    from apps.permisos.engine import permisos_de_usuario
    from apps.suscripciones.engine import modulos_negocio

    negocio = getattr(user, 'negocio', None)
    negocio_payload = None
    if negocio is not None:
        negocio_payload = {
            'id': negocio.id,
            'slug': negocio.slug,
            'nombre': negocio.nombre,
        }

    tenant_id = getattr(user, 'tenant_key', None)
    if tenant_id is None and negocio is not None:
        tenant_id = negocio.slug

    return {
        'id': user.id,
        'username': user.username,
        'email': getattr(user, 'email', ''),
        'first_name': getattr(user, 'first_name', ''),
        'last_name': getattr(user, 'last_name', ''),
        'full_name': (
            user.get_full_name()
            if hasattr(user, 'get_full_name')
            else user.username
        ),
        'rol': getattr(user, 'rol', None),
        'negocio': negocio_payload,
        'tenant_id': tenant_id,
        'permisos': sorted(permisos_de_usuario(user)),
        'modulos': sorted(modulos_negocio(negocio)),
    }
