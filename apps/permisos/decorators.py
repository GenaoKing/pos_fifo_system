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
"""
from functools import wraps

from django.http import JsonResponse
from django.shortcuts import redirect


def requiere_permiso_local(codigo, *, redirect_to='reportes:dashboard'):
    """
    Permite la vista solo si el usuario tiene el permiso `codigo`.

    - No autenticado -> login.
    - Autenticado sin el permiso -> `redirect_to` (por defecto el dashboard).

    Aplicar DESPUES de @login_required. El enforcement real vive en el motor
    RBAC (Usuario.tiene_permiso); SYSADMIN/ADMIN conservan acceso total.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user = getattr(request, 'user', None)
            if user is None or not user.is_authenticated:
                return redirect('usuarios:login')
            if not user.tiene_permiso(codigo):
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
            user = getattr(request, 'user', None)
            if user is None or not user.is_authenticated:
                return JsonResponse({'success': False, 'error': 'No autenticado.'}, status=401)
            if not user.tiene_permiso(codigo):
                return JsonResponse({'success': False, 'error': 'Permiso denegado.'}, status=403)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator
