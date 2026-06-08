"""
apps/permisos/engine.py
Resolucion y cache de permisos efectivos de un usuario.

API publica:
    permisos_de_usuario(usuario, sucursal=None) -> set[str]
    tiene_permiso(usuario, codigo, sucursal=None) -> bool
    invalidar_cache()  -> fuerza recalculo (lo llaman las signals)

Reglas:
    - Superusuario Django o rol legacy 'SYSADMIN' -> todos los permisos del catalogo.
    - Resto -> union de permisos de sus AsignacionRol activas (rol activo),
      respetando el scope de sucursal: asignaciones globales (sucursal NULL)
      siempre aplican; las acotadas solo aplican a su sucursal.

Cache:
    Se cachea por (usuario, sucursal) con una version global. Cualquier cambio
    en Rol / Rol.permisos / AsignacionRol / Permiso bumpea la version (via
    signals), invalidando todo de forma portable (sin wildcard delete).
"""
from django.core.cache import cache

CACHE_PREFIX = 'permisos_usuario'
CACHE_TIMEOUT = 300  # segundos; las signals invalidan antes en cambios
_VERSION_KEY = 'permisos_version'


def _version():
    v = cache.get(_VERSION_KEY)
    if v is None:
        v = 1
        cache.set(_VERSION_KEY, v, None)
    return v


def invalidar_cache():
    """Bumpea la version global -> invalida todas las entradas cacheadas."""
    try:
        cache.incr(_VERSION_KEY)
    except ValueError:
        # La key no existe todavia; inicializar en 2 (1 era el implicito).
        cache.set(_VERSION_KEY, 2, None)


def _cache_key(usuario_id, sucursal_id):
    return f'{CACHE_PREFIX}:v{_version()}:{usuario_id}:{sucursal_id or 0}'


def es_acceso_total(usuario):
    """
    Acceso total (todos los permisos del catalogo).

    Aplica a:
      - superusuario de Django.
      - rol legacy 'SYSADMIN' (operador del sistema) o 'ADMIN' (dueno del negocio).

    NOTA TRANSITORIA: incluir 'ADMIN' aqui preserva la conducta historica y
    evita lockouts durante la migracion. El control granular aplica a los roles
    operativos (cajeros y roles custom por negocio), que es el caso de uso. Una
    vez que todos los admins esten migrados a roles explicitos, quitar 'ADMIN'
    para poder restringir tambien al admin via configuracion del negocio.
    """
    if getattr(usuario, 'is_superuser', False):
        return True
    return getattr(usuario, 'rol', None) in ('ADMIN', 'SYSADMIN')


def permisos_de_usuario(usuario, sucursal=None):
    """Set de codigos de permiso efectivos del usuario en la sucursal dada."""
    if not usuario or not getattr(usuario, 'is_authenticated', False):
        return set()

    sucursal_id = getattr(sucursal, 'pk', None) if sucursal is not None else None
    key = _cache_key(usuario.pk, sucursal_id)
    cached = cache.get(key)
    if cached is not None:
        return cached

    if es_acceso_total(usuario):
        from .models import Permiso
        codigos = set(Permiso.objects.values_list('codigo', flat=True))
    else:
        codigos = _resolver_permisos(usuario, sucursal_id)

    cache.set(key, codigos, CACHE_TIMEOUT)
    return codigos


def _resolver_permisos(usuario, sucursal_id):
    from django.db.models import Q
    from .models import AsignacionRol, Permiso

    asignaciones = AsignacionRol.objects.filter(
        usuario=usuario,
        activo=True,
        rol__activo=True,
    )
    if sucursal_id is not None:
        # Globales (sucursal NULL) + las acotadas a esta sucursal.
        asignaciones = asignaciones.filter(
            Q(sucursal__isnull=True) | Q(sucursal_id=sucursal_id)
        )

    rol_ids = list(asignaciones.values_list('rol_id', flat=True).distinct())
    if not rol_ids:
        return set()

    return set(
        Permiso.objects.filter(roles__id__in=rol_ids)
        .values_list('codigo', flat=True)
        .distinct()
    )


def tiene_permiso(usuario, codigo, sucursal=None):
    """True si el usuario tiene el permiso `codigo` en la sucursal dada."""
    return codigo in permisos_de_usuario(usuario, sucursal)
