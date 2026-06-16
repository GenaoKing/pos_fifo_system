"""
apps/api/authentication.py
Autenticacion para la API REST.

Usa DRF Token Authentication estandar, extendida para inyectar la sucursal
asociada al usuario del token cuando el usuario es un "usuario de servicio"
de una sucursal.

Flujo:
    1. Cliente envia: Authorization: Token <key>
    2. DRF busca el Token en authtoken_token -> User
    3. Nuestra extension busca si ese User es usuario_servicio de alguna Sucursal
    4. Si si: inyecta sucursal en request.auth.sucursal
    5. Si no: el token es valido pero no representa una sucursal (ej: Santiago humano)

Con esto, un mismo endpoint puede aceptar ambos:
- Tokens de usuarios humanos (Santiago, admins): request.auth.sucursal = None
- Tokens de usuarios de servicio de sucursal: request.auth.sucursal = Sucursal(SD-001)

La permission class EsSucursalAutenticada decide cual es aceptable.
"""
from django.utils import timezone
from rest_framework.authentication import TokenAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from apps.tenancy.context import (
    bind_tenant_context_to_request,
    set_current_tenant,
    tenancy_enabled,
)
from apps.tenancy.models import SyncToken
from apps.tenancy.registry import configure_tenant_database


class SucursalTokenAuthentication(TokenAuthentication):
    """
    Extiende TokenAuthentication para inyectar la sucursal asociada al usuario.

    Si el usuario del token es usuario_servicio de una Sucursal activa,
    se inyecta esa sucursal en token.sucursal. Si no, token.sucursal = None
    (y la permission class EsSucursalAutenticada decide si eso es aceptable).
    """
    keyword = 'Token'

    def authenticate(self, request):
        if not tenancy_enabled():
            return super().authenticate(request)

        auth = get_authorization_header(request).split()
        if not auth or auth[0].lower() != self.keyword.lower().encode():
            return None

        if len(auth) == 1:
            raise AuthenticationFailed('Credenciales de token no provistas.')
        if len(auth) > 2:
            raise AuthenticationFailed('Token invalido. No debe contener espacios.')

        try:
            key = auth[1].decode()
        except UnicodeError:
            raise AuthenticationFailed('Token invalido.')

        return self.authenticate_credentials_for_tenant(key, request)

    def authenticate_credentials_for_tenant(self, key, request):
        token_hash = SyncToken.hash_token(key)
        sync_token = (
            SyncToken.objects.using('default')
            .select_related('tenant')
            .filter(token_hash=token_hash, activo=True, tenant__activo=True)
            .first()
        )
        if sync_token is None:
            raise AuthenticationFailed('Token de sucursal invalido o revocado.')

        tenant, alias = configure_tenant_database(sync_token.tenant)
        tokens = set_current_tenant(tenant.tenant_key, alias)
        bind_tenant_context_to_request(request, tokens)

        user, token = super().authenticate_credentials(key)
        token.tenant_key = tenant.tenant_key
        token.sucursal_codigo = sync_token.sucursal_codigo

        sync_token.ultimo_uso = timezone.now()
        sync_token.save(update_fields=['ultimo_uso'])

        user, token = self._attach_sucursal(
            user,
            token,
            expected_codigo=sync_token.sucursal_codigo,
        )
        request.sucursal = token.sucursal
        django_request = getattr(request, '_request', None)
        if django_request is not None:
            django_request.sucursal = token.sucursal
        return user, token

    def authenticate_credentials(self, key):
        # Resolver usuario via TokenAuthentication estandar de DRF
        user, token = super().authenticate_credentials(key)

        return self._attach_sucursal(user, token)

    def _attach_sucursal(self, user, token, expected_codigo=None):

        # Intentar encontrar la sucursal cuyo usuario_servicio es este user
        sucursal = None
        try:
            # Importacion diferida: evita circular imports al cargar DRF
            from apps.sucursales.models import Sucursal
            qs = Sucursal.objects.filter(
                usuario_servicio=user,
                activa=True,
            )
            if expected_codigo:
                qs = qs.filter(codigo=expected_codigo)
            sucursal = qs.first()
        except Exception:
            # Si el campo usuario_servicio no existe aun (migracion pendiente),
            # no rompas - simplemente deja sucursal en None.
            sucursal = None

        # Guardar sucursal en el token para uso en vistas/permissions
        # No modifica la tabla - solo el atributo en memoria de esta instancia
        token.sucursal = sucursal

        return (user, token)
