"""
apps/permisos/decorators.py
Decoradores de permiso para vistas Django del POS local.

Uso:
    @login_required
    @requiere_permiso_local('compras.registrar')
    def registrar_compra(request):
        ...

A diferencia del enforcement DRF (apps/api/permissions.py), estas vistas son
HTML server-rendered: ante falta de permiso se redirige (no se devuelve 403).

--------------------------------------------------------------------------
Scope de sucursal (PER-003)
--------------------------------------------------------------------------
Los dos decoradores llamaban `user.tiene_permiso(codigo)` sin sucursal, y en el
motor "sin sucursal" significaba "unir las asignaciones de TODAS las
sucursales". El resultado: un rol concedido unicamente en la sucursal A abria
los gates de la B, y era el camino por defecto — el que usan todas las vistas
del POS.

Ahora se resuelve la sucursal del request y se pasa explicita. En una
instalacion de una sola sucursal esto no cambia nada; en una BD compartida por
varias, cada gate responde por la sucursal en la que se esta operando.
"""
from functools import wraps

from django.http import JsonResponse
from django.shortcuts import redirect


def sucursal_del_request(request):
    """
    Sucursal en la que se esta operando, o `None`.

    `SucursalMiddleware` ya deja `request.sucursal`; se recalcula solo si el
    request no paso por el (comandos, tests que arman requests a mano).
    """
    sucursal = getattr(request, 'sucursal', False)
    if sucursal is not False:
        return sucursal

    from apps.tenancy.context import tenancy_enabled

    if tenancy_enabled():
        return None

    from apps.sucursales.models import get_sucursal_actual

    return get_sucursal_actual()


def _autorizado(request, codigo):
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return None, False
    return user, user.tiene_permiso(codigo, sucursal=sucursal_del_request(request))


def requiere_permiso_local(codigo, *, redirect_to='reportes:dashboard'):
    """
    Permite la vista solo si el usuario tiene el permiso `codigo` en la
    sucursal actual.

    - No autenticado -> login.
    - Autenticado sin el permiso -> `redirect_to` (por defecto el dashboard).

    Aplicar DESPUES de @login_required. El enforcement real vive en el motor
    RBAC (Usuario.tiene_permiso); SYSADMIN/ADMIN conservan acceso total.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user, ok = _autorizado(request, codigo)
            if user is None:
                return redirect('usuarios:login')
            if not ok:
                return redirect(redirect_to)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def requiere_permiso_json(codigo):
    """
    Variante para endpoints JSON consumidos via fetch(): ante falta de
    permiso devuelve 403 JSON en vez de redirigir (el redirect de
    `requiere_permiso_local` rompe los clientes fetch).
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user, ok = _autorizado(request, codigo)
            if user is None:
                return JsonResponse(
                    {'success': False, 'error': 'No autenticado.'}, status=401,
                )
            if not ok:
                return JsonResponse(
                    {'success': False, 'error': 'Permiso denegado.'}, status=403,
                )
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator
