"""
apps/api/authentication.py
Autenticación para la API REST.

Fase actual: Token Authentication estándar de DRF.
Cada sucursal usa un usuario de servicio con su propio token.

Fase futura (post Fase 2): SucursalTokenAuthentication que inyecta
request.sucursal automáticamente basado en el token.

Uso desde sucursal:
    Authorization: Token abc123def456...
"""

from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed


class SucursalTokenAuthentication(TokenAuthentication):
    """
    Extiende TokenAuthentication para inyectar la sucursal en el request.
    
    Fase actual: funciona como TokenAuthentication estándar.
    
    TODO: FASE 2 — Una vez que exista el modelo Sucursal:
    - Buscar la sucursal asociada al usuario del token
    - Inyectar request.sucursal
    - Rechazar si el usuario no tiene sucursal asignada
    """
    keyword = 'Token'  # Header: Authorization: Token <key>

    def authenticate_credentials(self, key):
        user, token = super().authenticate_credentials(key)

        # TODO: FASE 2 — Descomentar cuando exista Sucursal
        # ──────────────────────────────────────────────────
        # from apps.sucursales.models import Sucursal
        #
        # try:
        #     sucursal = Sucursal.objects.get(
        #         usuario_servicio=user,
        #         activa=True
        #     )
        # except Sucursal.DoesNotExist:
        #     raise AuthenticationFailed(
        #         'Token válido pero sin sucursal asignada.'
        #     )
        #
        # # Inyectar sucursal en el token para uso posterior
        # token.sucursal = sucursal
        # ──────────────────────────────────────────────────

        return (user, token)