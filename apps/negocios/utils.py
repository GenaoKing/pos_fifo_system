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
    - Principal GLOBAL (SYSADMIN o superusuario) sin negocio -> puede apuntar a
      un negocio explícito con ?negocio=<id> para administrar cualquier tenant.

    El docstring anterior ya decía "para SYSADMIN/superuser", pero el código
    solo comprobaba que `user.negocio_id` fuera nulo. Cualquier usuario sin
    negocio —incluido uno ordinario al que alguien le colgó una asignación—
    podía elegir el negocio que quisiera y administrarle el RBAC. La auditoría
    reprodujo la escalada de extremo a extremo: un ADMIN del negocio A le asigna
    un rol a un usuario con `negocio=NULL`, y ese usuario pide
    `?negocio=<B>` y recibe 200 con los roles privados de B (PER-005).
    """
    user = getattr(request, 'user', None)
    if user is None or not getattr(user, 'is_authenticated', False):
        return None

    if not getattr(user, 'activo', True):
        return None

    if getattr(user, 'negocio_id', None):
        return user.negocio

    if not es_principal_global(user):
        # Sin negocio propio y sin identidad global no hay tenant que resolver.
        # Devolver None es fail-closed: los querysets quedan vacíos y los gates
        # de administración no encuentran negocio sobre el cual operar.
        return None

    negocio_id = _query_param(request, 'negocio')
    if negocio_id:
        from .models import Negocio
        return Negocio.objects.filter(pk=negocio_id, activo=True).first()

    return None


def es_principal_global(user):
    """
    True si `user` opera la plataforma, no un negocio.

    Se acepta el superusuario de Django, el rol legacy SYSADMIN y una identidad
    global del control plane (`is_global`), que es como el portal cloud marca a
    los operadores bajo DB-per-tenant.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    if getattr(user, 'rol', None) == 'SYSADMIN':
        return True
    identidad = getattr(user, 'identity', None)
    return bool(getattr(identidad, 'is_global', False))


def _query_param(request, nombre):
    """Lee un query param tanto de un DRF Request como de un HttpRequest."""
    params = getattr(request, 'query_params', None)
    if params is None:
        params = getattr(request, 'GET', None)
    return params.get(nombre) if params is not None else None
