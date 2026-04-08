"""
apps/configuracion/decorators.py
Decoradores para proteger vistas por modulo activo y rol.

Uso:
    @requiere_modulo('financiacion_coop')
    def vista_financiacion(request):
        ...

    @requiere_sysadmin
    def panel_configuracion(request):
        ...
"""
from functools import wraps
from django.http import Http404
from django.shortcuts import redirect
from .utils import modulo_activo


def requiere_modulo(nombre_modulo):
    """
    Decorador que retorna 404 si el modulo no esta activo.
    Se aplica DESPUES de @login_required.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not modulo_activo(nombre_modulo):
                raise Http404
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def requiere_sysadmin(view_func):
    """
    Decorador que solo permite acceso a usuarios con rol SYSADMIN.
    Redirige al dashboard si no tiene permisos.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return redirect('login')
        if not getattr(request.user, 'es_sysadmin', False):
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper


def requiere_admin_o_sysadmin(view_func):
    """
    Decorador que permite acceso a ADMIN y SYSADMIN.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return redirect('login')
        user = request.user
        es_admin = getattr(user, 'es_admin', False)
        es_sysadmin = getattr(user, 'es_sysadmin', False)
        # es_admin puede ser property o method
        if callable(es_admin):
            es_admin = es_admin()
        if callable(es_sysadmin):
            es_sysadmin = es_sysadmin()
        if not (es_admin or es_sysadmin):
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper