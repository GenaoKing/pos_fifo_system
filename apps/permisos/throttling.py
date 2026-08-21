"""
apps/permisos/throttling.py
Freno de intentos para las credenciales de autorizacion del POS.

Por que hace falta: un codigo de carnet es corto y el endpoint que lo valida
esta abierto a cualquier usuario con sesion en el POS. Sin freno, el cajero al
que justamente se le quiere poner el control puede iterar codigos desde la
consola del navegador hasta encontrar uno valido y autoautorizarse descuentos
toda la tarde.

Los throttles de `apps/api/throttling.py` son de DRF y no aplican: las vistas
del POS local son vistas Django planas.

La clave combina IP y usuario de la sesion. El endpoint exige `@login_required`,
asi que el usuario es el eje fuerte: un atacante tiene que estar logueado para
probar, y su presupuesto no lo comparte con nadie. La IP suma para el caso de
varias sesiones desde la misma terminal.

NOTA: con `LocMemCache` el contador es por proceso. En la sucursal el POS corre
en un solo proceso y alcanza; si algun dia se despliega con varios workers,
esto necesita un backend de cache compartido (Redis) para contar de verdad.
"""
from django.core.cache import cache

# Presupuesto deliberadamente corto: un supervisor pasa el carnet una vez, y
# si falla lo pasa de nuevo. Nadie legitimo necesita 10 intentos en 5 minutos.
INTENTOS_MAX = 8
VENTANA_SEGUNDOS = 300

_PREFIJO = 'permisos:intentos_credencial'


def _ip(request):
    reenviada = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if reenviada:
        return reenviada.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '') or 'sin-ip'


def clave(request):
    usuario_id = getattr(getattr(request, 'user', None), 'pk', None) or 'anon'
    return f'{_PREFIJO}:{usuario_id}:{_ip(request)}'


def excedido(request):
    """True si esta terminal ya agoto su presupuesto de intentos fallidos."""
    return (cache.get(clave(request)) or 0) >= INTENTOS_MAX


def registrar_fallo(request):
    """Suma un intento fallido dentro de la ventana."""
    k = clave(request)
    # `add` solo escribe si la clave no existe: fija el TTL en el primer fallo
    # para que la ventana sea deslizante desde ahi y no se renueve sola con
    # cada intento (si no, un atacante lento nunca expiraria el contador).
    cache.add(k, 0, VENTANA_SEGUNDOS)
    try:
        cache.incr(k)
    except ValueError:
        # La clave expiro entre el `add` y el `incr`.
        cache.set(k, 1, VENTANA_SEGUNDOS)


def limpiar(request):
    """Un intento exitoso borra el historial de fallos de esa terminal."""
    cache.delete(clave(request))
