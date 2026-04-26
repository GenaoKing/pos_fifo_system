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
from rest_framework.authentication import TokenAuthentication


class SucursalTokenAuthentication(TokenAuthentication):
    """
    Extiende TokenAuthentication para inyectar la sucursal asociada al usuario.

    Si el usuario del token es usuario_servicio de una Sucursal activa,
    se inyecta esa sucursal en token.sucursal. Si no, token.sucursal = None
    (y la permission class EsSucursalAutenticada decide si eso es aceptable).
    """
    keyword = 'Token'

    def authenticate_credentials(self, key):
        # Resolver usuario via TokenAuthentication estandar de DRF
        user, token = super().authenticate_credentials(key)

        # Intentar encontrar la sucursal cuyo usuario_servicio es este user
        sucursal = None
        try:
            # Importacion diferida: evita circular imports al cargar DRF
            from apps.sucursales.models import Sucursal
            sucursal = Sucursal.objects.filter(
                usuario_servicio=user,
                activa=True,
            ).first()
        except Exception:
            # Si el campo usuario_servicio no existe aun (migracion pendiente),
            # no rompas - simplemente deja sucursal en None.
            sucursal = None

        # Guardar sucursal en el token para uso en vistas/permissions
        # No modifica la tabla - solo el atributo en memoria de esta instancia
        token.sucursal = sucursal

        return (user, token)