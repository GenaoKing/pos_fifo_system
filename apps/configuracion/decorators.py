"""
apps/configuracion/decorators.py
Decoradores para gatear vistas por modulo activo (entitlement comercial).

Uso:
    @requiere_modulo('financiacion_coop')
    def vista_financiacion(request):
        ...
"""
from functools import wraps
from django.http import Http404, JsonResponse
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


def requiere_modulo_json(nombre_modulo):
    """
    Variante de `requiere_modulo` para endpoints JSON consumidos via fetch():
    ante un modulo inactivo devuelve 404 JSON en vez de la pagina HTML de Http404
    (que rompe los clientes fetch).

    Es el gate de MODULO (entitlement comercial); es ORTOGONAL al permiso RBAC
    (SUS-006 / CT-02): ambos deben aprobar. Se aplica DESPUES de @login_required
    y del gate de permiso, para que una URL guardada no conserve la funcion
    cuando el plan ya no incluye el modulo.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not modulo_activo(nombre_modulo):
                return JsonResponse(
                    {'success': False, 'error': 'Modulo no disponible.'},
                    status=404,
                )
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator