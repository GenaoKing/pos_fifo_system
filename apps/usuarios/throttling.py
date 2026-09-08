"""
apps/usuarios/throttling.py

Freno de fuerza bruta para el login del POS local (USR-006).

El portal cloud ya tenia throttles (`apps/api/throttling.py`), pero son de DRF y
protegen `/auth/login/`. La vista Django `/login/` no tenia ninguno: doce
passwords incorrectos consecutivos devolvian la pantalla normal, creaban doce
filas de auditoria y no bloqueaban un login valido inmediato.

Dos ventanas, igual que en el portal:

- **Rafaga** — corta el ataque rapido.
- **Sostenida** — corta el goteo lento que espera a que expire la ventana corta.

La clave combina IP y username, y el motivo es el mismo que en el portal:

- Solo por IP, un NAT o el proxy bloquean a todos los usuarios de esa salida.
- Solo por username, cualquiera puede dejar fuera a un operador conocido
  mandando basura con su nombre — denegacion de servicio dirigida.

Con la combinacion, cada par (IP, username) tiene su propio presupuesto.

Se apoya en el cache de Django. Con `LocMemCache` el conteo es por worker, asi
que el limite efectivo se multiplica por el numero de procesos: sigue frenando
—de 5 intentos pasa a 15 con tres workers— pero un backend compartido lo hace
exacto. Es la misma recomendacion de Redis que dejo la auditoria de permisos.
"""
from django.core.cache import cache

PREFIJO = 'login_local'


class LimiteLogin:
    """Contador de intentos fallidos con dos ventanas."""

    def __init__(self, rafaga=(5, 60), sostenido=(20, 900)):
        self.rafaga_max, self.rafaga_segundos = rafaga
        self.sostenido_max, self.sostenido_segundos = sostenido

    # -- API ---------------------------------------------------------

    def consultar(self, request, username):
        """Estado actual del par (IP, username), sin modificarlo."""
        rafaga = cache.get(self._clave(request, username, 'rafaga'), 0)
        sostenido = cache.get(self._clave(request, username, 'sostenido'), 0)
        return _Estado(
            rafaga=rafaga,
            sostenido=sostenido,
            excedido=(rafaga >= self.rafaga_max or sostenido >= self.sostenido_max),
        )

    def registrar_fallo(self, request, username):
        """Suma uno a las dos ventanas."""
        self._incrementar(
            self._clave(request, username, 'rafaga'), self.rafaga_segundos,
        )
        self._incrementar(
            self._clave(request, username, 'sostenido'), self.sostenido_segundos,
        )

    def limpiar(self, request, username):
        """Un login exitoso borra el historial de fallos de ese par."""
        cache.delete(self._clave(request, username, 'rafaga'))
        cache.delete(self._clave(request, username, 'sostenido'))

    # -- Interno -----------------------------------------------------

    def _incrementar(self, clave, segundos):
        # `add` + `incr` en vez de get/set: `incr` es atomico en los backends
        # que lo soportan, asi que dos requests simultaneos no pierden cuentas.
        if cache.add(clave, 1, segundos):
            return 1
        try:
            return cache.incr(clave)
        except ValueError:
            # Expiro entre el `add` y el `incr`.
            cache.set(clave, 1, segundos)
            return 1

    def _clave(self, request, username, ventana):
        ip = _ip_del_request(request)
        usuario = (username or '').strip().lower()
        return f'{PREFIJO}:{ventana}:{ip}:{usuario}'


class _Estado:
    __slots__ = ('rafaga', 'sostenido', 'excedido')

    def __init__(self, rafaga, sostenido, excedido):
        self.rafaga = rafaga
        self.sostenido = sostenido
        self.excedido = excedido


def _ip_del_request(request):
    """
    IP del cliente para el conteo.

    A proposito NO se lee `X-Forwarded-For`: cualquiera puede enviarlo, y
    confiar en el convierte el freno en un contador que el atacante reinicia a
    voluntad cambiando una cabecera. Detras de un proxy, `REMOTE_ADDR` es el del
    proxy y la parte IP de la clave pierde resolucion — la parte del username
    sigue aplicando, que es la que corta el ataque dirigido. Ver USR-014 para el
    problema equivalente en la auditoria.
    """
    return request.META.get('REMOTE_ADDR', '') or 'sin-ip'


limite_login = LimiteLogin()
