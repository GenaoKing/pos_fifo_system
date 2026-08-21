"""
apps/api/throttling.py

Throttles del portal cloud.

El unico throttle configurado era `ScopedRateThrottle` y no existia scope de
auth, asi que `/auth/login/` no tenia freno de aplicacion: fuerza bruta y
credential stuffing sin limite, y cada intento pagando el costo de un hash de
password.
"""
from rest_framework.throttling import SimpleRateThrottle


class _LoginThrottleBase(SimpleRateThrottle):
    """
    Base de los throttles de login.

    La clave combina IP y email normalizado. Por que las dos:

    - Solo por IP: un NAT corporativo o el proxy de Azure comparten IP, y un
      atacante bloquearia a todos los usuarios legitimos de esa salida.
    - Solo por email: un atacante rota emails y nunca toca el limite; ademas
      cualquiera puede bloquear a un usuario conocido enviando basura con su
      email (denegacion de servicio dirigida).

    Con la combinacion, cada par (IP, email) tiene su propio presupuesto: la
    rafaga se corta sin que un tercero pueda bloquear a nadie.

    NOTA de despliegue: detras del proxy de Azure, `REMOTE_ADDR` es el del
    proxy. Para que la parte IP sirva de verdad hace falta `USE_X_FORWARDED_HOST`
    / `X-Forwarded-For` confiable. Mientras tanto, la parte del email sigue
    aplicando y es la que corta el ataque dirigido.
    """

    def get_cache_key(self, request, view):
        email = ''
        datos = getattr(request, 'data', None)
        if isinstance(datos, dict):
            email = str(datos.get('email') or datos.get('username') or '').strip().lower()

        ident = self.get_ident(request)
        return self.cache_format % {
            'scope': self.scope,
            'ident': f'{ident}:{email}',
        }

    def throttle_failure(self):
        """
        No revela si el email existe: el 429 es identico para cualquier email.
        """
        return super().throttle_failure()


class LoginRafagaThrottle(_LoginThrottleBase):
    """Ventana corta: corta el ataque en rafaga."""
    scope = 'login'


class LoginSostenidoThrottle(_LoginThrottleBase):
    """Ventana larga: corta el goteo lento que evade la ventana corta."""
    scope = 'login_sostenido'
