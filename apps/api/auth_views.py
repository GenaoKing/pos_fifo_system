import logging

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
    TokenRefreshSerializer,
)
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.views import TokenRefreshView

from apps.api.throttling import LoginRafagaThrottle, LoginSostenidoThrottle
from apps.tenancy.authentication import _autorizar_tenant
from apps.tenancy.context import tenant_context, tenancy_enabled
from apps.tenancy.models import (
    Identity,
    Membership,
    SesionImpersonacion,
    Tenant,
)

logger = logging.getLogger('tenancy.auth')


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
    # Rate limit del login (TEN-009). Sin esto el endpoint aceptaba intentos
    # ilimitados: quince passwords incorrectos daban quince 400 y ningun 429.
    throttle_classes = [LoginRafagaThrottle, LoginSostenidoThrottle]

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
    motivo = (request.data.get('motivo') or '').strip()
    if not tenant_key:
        return Response({'detail': 'tenant_key es requerido.'}, status=400)
    if not motivo:
        # Exigir motivo hace la traza util y obliga al operador a declarar por
        # que entra a la base de un cliente.
        return Response(
            {'detail': 'motivo es requerido para impersonar (ticket o razon).'},
            status=400,
        )

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
        # `impersonado=True` marca el token para que la autenticacion NO exija
        # una Membership (el operador global no la tiene): en su lugar revalida
        # que el Identity siga siendo global. Sin esta distincion, exigir
        # membership romperia el soporte, y no exigirla nunca dejaba pasar
        # cualquier token viejo.
        payload = _tenant_token_payload(
            request.user.identity, tenant, user, impersonado=True,
        )

    # Rastro DURABLE en el control plane, no solo un log.
    sesion = SesionImpersonacion.objects.using('default').create(
        identity=request.user.identity,
        tenant=tenant,
        username_objetivo=user.username,
        motivo=motivo[:300],
        ip_address=_ip_cliente(request),
        expira=timezone.now() + api_settings.REFRESH_TOKEN_LIFETIME,
    )

    logger.warning(
        'IMPERSONACION #%s: identity=%s (%s) actuando como %s en tenant %s. Motivo: %s',
        sesion.pk, request.user.identity.pk, request.user.identity.email,
        user.username, tenant.tenant_key, motivo,
    )
    payload['impersonacion_id'] = sesion.pk
    return Response(payload)


def _ip_cliente(request):
    reenviada = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if reenviada:
        return reenviada.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


class TenantTokenRefreshSerializer(TokenRefreshSerializer):
    """
    Refresh que revalida el estado ACTUAL, no el del momento del login.

    `TokenRefreshView` generico solo comprobaba la firma y el vencimiento: tras
    eliminar la membership, el mismo refresh seguia emitiendo access tokens
    nuevos. Revocar el vinculo con el tenant no detenia la sesion.

    Aplica exactamente la misma regla que la autenticacion
    (`_autorizar_tenant`), porque de nada sirve cortar el access si el refresh
    puede fabricar otro.
    """

    def validate(self, attrs):
        refresh = RefreshToken(attrs['refresh'])
        identity_id = refresh.get('identity_id')
        tenant_key = refresh.get('tenant_key') or refresh.get('tenant_id')

        # Resolver el tenant ANTES de llamar a la base de simplejwt es lo que
        # arregla BUG-E. `TokenRefreshSerializer.validate()` (la clase padre)
        # resuelve el usuario del token con `Usuario.objects.get(id=...)`; bajo
        # DB-per-tenant `Usuario` es dual-home y sin contexto activo esa
        # consulta cae al control plane, donde el usuario no existe. Antes
        # `super().validate()` corria primero y el `DoesNotExist` subia sin
        # capturar -> 500 en vez de 401. Ahora el tenant se activa alrededor de
        # la llamada, y lo que SI puede pasar (usuario borrado de verdad) se
        # atrapa explicito.
        tenant = None
        if identity_id and tenant_key:
            tenant = Tenant.objects.using('default').filter(
                tenant_key=tenant_key, activo=True,
            ).first()
            if tenant is None:
                raise InvalidToken('El tenant ya no esta activo.')

        try:
            if tenant is not None:
                with tenant_context(tenant):
                    data = super().validate(attrs)
            else:
                data = super().validate(attrs)
        except ObjectDoesNotExist:
            raise InvalidToken('El usuario del token ya no existe.')

        if not identity_id:
            # Token legacy (sin tenancy): se comporta como antes.
            return data

        identity = Identity.objects.using('default').filter(
            pk=identity_id, activo=True,
        ).first()
        if identity is None:
            raise InvalidToken('La identidad ya no esta activa.')

        if not tenant_key:
            if identity.is_global and refresh.get('is_global'):
                return data
            raise InvalidToken('Token sin tenant.')

        try:
            _autorizar_tenant(
                identity=identity,
                tenant=tenant,
                username=refresh.get('username'),
                impersonado=bool(refresh.get('impersonado')),
            )
        except AuthenticationFailed as exc:
            raise InvalidToken(str(exc.detail))

        return data


class TenantTokenRefreshView(TokenRefreshView):
    serializer_class = TenantTokenRefreshSerializer


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout(request):
    """
    Logout server-side: invalida el refresh recibido.

    Antes no existia. "Cerrar sesion" solo borraba la copia del navegador; una
    copia robada seguia viva hasta siete dias y podia canjearse cuantas veces
    quisiera.

    Idempotente a proposito: un refresh ya invalidado o mal formado devuelve
    204 igual. El cliente no puede hacer nada distinto con un error, y
    responder 400 solo filtra si el token era valido.
    """
    raw = (request.data.get('refresh') or '').strip()
    if not raw:
        return Response(status=status.HTTP_204_NO_CONTENT)

    try:
        RefreshToken(raw).blacklist()
    except AttributeError:
        # `token_blacklist` no instalada: mejor saberlo que fingir un logout.
        logger.error(
            'Logout solicitado pero token_blacklist no esta instalada; '
            'el refresh sigue siendo valido.'
        )
        return Response(
            {'detail': 'Logout no disponible: blacklist no configurada.'},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )
    except TokenError:
        # Ya vencido, ya en blacklist o invalido: el objetivo se cumple igual.
        pass

    # Si la sesion era impersonada, se cierra su rastro: sin esto la traza
    # muestra un inicio sin fin y no se sabe cuanto duro el acceso.
    if getattr(request.user, 'es_impersonado', False):
        SesionImpersonacion.objects.using('default').filter(
            identity_id=getattr(request.user, 'identity_id', None),
            username_objetivo=request.user.username,
            cierre__isnull=True,
        ).update(cierre=timezone.now())

    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def perfil_actual(request):
    return Response(_user_payload(request.user))


def _tenant_token_payload(identity, tenant, user, *, impersonado=False):
    user.tenant_key = tenant.tenant_key
    user.identity_id = identity.pk
    refresh = RefreshToken.for_user(user)
    _add_tenant_claims(refresh, identity, tenant, user, impersonado=impersonado)
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


def _add_tenant_claims(token, identity, tenant, user, *, impersonado=False):
    token['identity_id'] = identity.pk
    token['impersonado'] = impersonado
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

    # Antes: solo ADMIN/SYSADMIN. Con RBAC granular ya operativo (apps.permisos)
    # el gate correcto no es el rol sino tener algun permiso concreto -- es lo
    # que abre el portal a la cajera (BUG-G, docs/BUGS.md: fotografiar
    # productos desde el celular) sin darle acceso a nada mas de lo que su
    # rol ya le concede. Un usuario sin ninguna asignacion de rol activa
    # sigue sin poder entrar.
    from apps.permisos.engine import TODAS, permisos_de_usuario

    # `TODAS` explicito: la pregunta aca es "¿tiene algun permiso en alguna
    # parte?", que es un caso legitimo de union del negocio completo. El scope
    # por defecto pasa a ser "solo asignaciones globales" (PER-003), y con el
    # una cajera con rol acotado a su sucursal no podria entrar al portal.
    if not permisos_de_usuario(user, sucursal=TODAS):
        raise serializers.ValidationError(
            {'detail': 'Su usuario no tiene ningun permiso asignado para el '
                       'portal cloud. Contacte al administrador.'},
            code='sin_permisos_portal',
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

    from apps.permisos.engine import TODAS, permisos_de_usuario
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
        # PISTA PARA LA UI, no enforcement: es la union de lo que el usuario
        # puede en alguna sucursal, para que el portal sepa que menus dibujar.
        # Cada endpoint revalida con el scope real (apps/api/permissions.py);
        # que un boton aparezca no significa que la accion vaya a pasar.
        'permisos': sorted(permisos_de_usuario(user, sucursal=TODAS)),
        'modulos': sorted(modulos_negocio(negocio)),
    }
