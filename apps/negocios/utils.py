"""
apps/negocios/utils.py
Resolución del tenant (Negocio) del request.

`negocio_actual(request)` es el ÚNICO punto de resolución de tenant. Usarlo
siempre para scopear querysets/permisos en vez de leer `request.user.negocio`
disperso por el código.

Forward-compat con django-tenants (schema-per-tenant): cuando entre, este helper
pasa a significar "el schema actual" (lo fija un middleware) y los filtros
`negocio=...` desaparecen en un solo lugar.
"""


def negocio_actual(request):
    """
    Retorna el Negocio del request, o None si no se puede determinar.

    - Usuario con negocio asignado -> su negocio.
    - Usuario global (SYSADMIN/superuser sin negocio) -> puede apuntar a un
      negocio explícito con ?negocio=<id> para administrar cualquier tenant.
    """
    user = getattr(request, 'user', None)
    if user is None or not getattr(user, 'is_authenticated', False):
        return None

    if getattr(user, 'negocio_id', None):
        return user.negocio

    # Usuario global: permite elegir negocio por query param.
    negocio_id = _query_param(request, 'negocio')
    if negocio_id:
        from .models import Negocio
        return Negocio.objects.filter(pk=negocio_id).first()

    return None


def _query_param(request, nombre):
    """Lee un query param tanto de un DRF Request como de un HttpRequest."""
    params = getattr(request, 'query_params', None)
    if params is None:
        params = getattr(request, 'GET', None)
    return params.get(nombre) if params is not None else None
