from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from .context import set_current_tenant, tenancy_enabled
from .models import Identity
from .registry import configure_tenant_database


@dataclass
class IdentityPrincipal:
    identity: Identity

    is_authenticated = True
    is_active = True
    rol = 'SYSADMIN'
    is_staff = True
    is_superuser = True
    negocio = None
    negocio_id = None
    is_global_identity = True

    @property
    def id(self):
        return self.identity.pk

    @property
    def pk(self):
        return self.identity.pk

    @property
    def username(self):
        return self.identity.email

    @property
    def email(self):
        return self.identity.email

    def get_full_name(self):
        return self.identity.nombre or self.identity.email

    def tiene_permiso(self, permiso, sucursal=None):
        return True


class TenantJWTAuthentication(JWTAuthentication):
    """SimpleJWT auth that activates the tenant DB before loading Usuario."""

    def authenticate(self, request):
        if not tenancy_enabled():
            return super().authenticate(request)

        header = self.get_header(request)
        if header is None:
            return None

        raw_token = self.get_raw_token(header)
        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)
        user = self.get_user_for_token(validated_token, request)
        return user, validated_token

    def get_user_for_token(self, validated_token, request):
        identity_id = validated_token.get('identity_id')
        tenant_key = validated_token.get('tenant_key') or validated_token.get('tenant_id')

        if not identity_id:
            raise AuthenticationFailed('Token sin identity_id.', code='no_identity')

        identity = Identity.objects.using('default').filter(
            pk=identity_id,
            activo=True,
        ).first()
        if identity is None:
            raise AuthenticationFailed('Identity inactiva o inexistente.', code='identity_not_found')

        if not tenant_key:
            if identity.is_global and validated_token.get('is_global'):
                return IdentityPrincipal(identity)
            raise AuthenticationFailed('Token sin tenant_key.', code='no_tenant')

        try:
            tenant, alias = configure_tenant_database(tenant_key)
        except Exception as exc:
            raise AuthenticationFailed('Tenant invalido o inactivo.', code='tenant_invalid') from exc

        tokens = set_current_tenant(tenant.tenant_key, alias)
        request._tenant_context_tokens = tokens

        username = validated_token.get('username')
        if not username:
            raise AuthenticationFailed('Token sin username operativo.', code='no_username')

        User = get_user_model()
        user = User.objects.filter(username=username, activo=True).first()
        if user is None:
            raise AuthenticationFailed('Usuario operativo inactivo o inexistente.', code='user_not_found')

        user.identity_id = identity.pk
        user.tenant_key = tenant.tenant_key
        return user


def touch_identity(identity):
    identity.ultimo_acceso = timezone.now()
    identity.save(update_fields=['ultimo_acceso'])
