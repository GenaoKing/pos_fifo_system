"""
apps/permisos/engine.py
Resolucion y cache de permisos efectivos de un usuario.

API publica:
    tiene_permiso(usuario, codigo, sucursal=None) -> bool
    permisos_de_usuario(usuario, sucursal=None)   -> set[str]
    sucursales_con_permiso(usuario, codigo)       -> None | set[int]
    invalidar_cache()                             -> fuerza recalculo
    TODAS                                          -> centinela de scope

Reglas:
    - Superusuario Django o rol legacy 'SYSADMIN'/'ADMIN' -> acceso total, con
      dos excepciones: un codigo que no existe en el catalogo SIEMPRE deniega,
      y las capacidades del operador SaaS no las alcanza un admin de tenant.
    - Resto -> union de permisos de sus AsignacionRol activas, respetando el
      scope de sucursal y el negocio del usuario.

--------------------------------------------------------------------------
Que significa `sucursal` (PER-003)
--------------------------------------------------------------------------
Antes `None` no queria decir "sin scope": queria decir "unir las asignaciones
de TODAS las sucursales". Como los decoradores, el filtro de plantilla y los
requests de portal sin token de sucursal llamaban sin scope, un rol concedido
unicamente en la sucursal A habilitaba los gates de la B. El valor por defecto
era el menos conservador de los tres posibles.

Ahora:

    sucursal=None       -> SOLO asignaciones globales (sucursal NULL).
    sucursal=<Sucursal> -> globales + las acotadas a esa sucursal.
    sucursal=TODAS      -> union de todo el negocio. Hay que pedirlo por su
                           nombre; existe para preguntas legitimas del tipo
                           "¿este usuario puede algo en alguna parte?".

--------------------------------------------------------------------------
Cache
--------------------------------------------------------------------------
Dos problemas convivian aca.

1. La clave era `permisos_usuario:v<n>:<usuario_id>:<sucursal_id>` y nada mas.
   Los PK se reinician en cada base tenant, asi que el usuario 777 de un
   negocio y el 777 de otro compartian entrada dentro del mismo worker
   (PER-001). Ahora la clave lleva el namespace del tenant activo.

2. El backend por defecto es `LocMemCache`, que es privado de cada proceso, y
   la imagen productiva arranca Gunicorn con `--workers 3` (PER-002). Revocar
   un permiso invalidaba el worker que atendio la escritura; los otros dos
   seguian autorizando hasta 300 segundos. El comentario original describia
   Azure como single-worker, cosa que el Dockerfile contradice.

   La respuesta no es bajar el TTL —eso reduce la ventana, no la cierra—, sino
   dejar de cachear ENTRE requests cuando el backend no se comparte. Con un
   backend local, el cache pasa a ser un memo por request: cada request resuelve
   como mucho una vez por (usuario, sucursal), que es donde esta casi todo el
   beneficio —decoradores y plantillas preguntan muchas veces dentro del mismo
   request— y ningun worker puede quedarse con una decision vieja.

   Si se configura un backend compartido (Redis/memcached), se vuelve a cachear
   entre requests y la invalidacion por version sirve para todos los procesos.
"""
import logging
from contextvars import ContextVar

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger('permisos')

CACHE_PREFIX = 'permisos_usuario'
CACHE_TIMEOUT = 300  # segundos; las signals invalidan antes en cambios
_VERSION_KEY = 'permisos_version'

# Backends que NO se comparten entre procesos.
_BACKENDS_LOCALES = ('locmem', 'dummy')


class _Todas:
    """Centinela: union de las asignaciones de todo el negocio."""

    __slots__ = ()

    def __repr__(self):  # pragma: no cover - diagnostico
        return 'TODAS'


TODAS = _Todas()

# Memo por request. Se limpia en `PermisosRequestCacheMiddleware`; la clave
# incluye la version global, asi que `invalidar_cache()` tambien lo descarta
# dentro del mismo request (y en procesos sin middleware, como los comandos).
_memo = ContextVar('permisos_memo', default=None)


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
    limpiar_memo()


def limpiar_memo():
    """Descarta el memo del request en curso."""
    _memo.set(None)


def _cache_compartido():
    """True si el backend de cache lo ven todos los workers."""
    backend = (
        settings.CACHES.get('default', {}).get('BACKEND', '')
        if hasattr(settings, 'CACHES') else ''
    ).lower()
    return not any(local in backend for local in _BACKENDS_LOCALES)


def _namespace():
    """
    Identidad del contexto de datos actual.

    Sin esto, dos bases tenant con PK coincidentes comparten entrada de cache.
    No se deriva de `Negocio.pk` a proposito: ese PK tambien es local a cada
    base. Se usa el `tenant_key`, que es tecnico e inmutable.
    """
    from apps.tenancy.context import (
        TenantContextError,
        get_current_tenant_key,
        tenancy_enabled,
    )

    if not tenancy_enabled():
        return 'local'

    key = get_current_tenant_key()
    if not key:
        # Fail-loud, igual que el router y el prefijo de media: bajo tenancy,
        # resolver permisos sin tenant activo es un bug de contexto. Degradar
        # al namespace implicito es justamente lo que mezclaba negocios.
        raise TenantContextError(
            'Se pidieron permisos sin tenant activo en contexto.'
        )
    return key


def _scope_id(sucursal):
    """Fragmento de clave que identifica el scope pedido."""
    if sucursal is TODAS:
        return 'todas'
    if sucursal is None:
        return 'global'
    return str(getattr(sucursal, 'pk', sucursal))


def _cache_key(namespace, usuario_id, sucursal):
    return (
        f'{CACHE_PREFIX}:v{_version()}:{namespace}:'
        f'{usuario_id}:{_scope_id(sucursal)}'
    )


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

    Ojo: "acceso total" NO es incondicional. `tiene_permiso` mantiene dos
    limites por encima de este atajo — el catalogo y las capacidades del
    operador SaaS. Ver ahi.
    """
    if not _usuario_habilitado(usuario):
        return False
    if getattr(usuario, 'is_superuser', False):
        return True
    return getattr(usuario, 'rol', None) in ('ADMIN', 'SYSADMIN')


def es_operador_global(usuario):
    """
    True solo para el operador del SaaS, no para el dueno de un negocio.

    `ADMIN` es el administrador de UN tenant; `SYSADMIN` y el superusuario
    operan la plataforma. Distinguirlos es lo que impide que un admin llegue a
    los controles comerciales (planes, suscripciones, overrides) que el
    catalogo describe como externos a su negocio.
    """
    if not _usuario_habilitado(usuario):
        return False
    if getattr(usuario, 'is_superuser', False):
        return True
    return getattr(usuario, 'rol', None) == 'SYSADMIN'


def _usuario_habilitado(usuario):
    """
    Un usuario desactivado no tiene permisos, tenga la sesion que tenga.

    El motor solo miraba `is_authenticated`. Y `Usuario` define `activo` pero
    heredaba `is_active = True` de `AbstractBaseUser`, asi que Django tampoco lo
    frenaba: una sesion ya abierta sobrevivia a la desactivacion (PER-010).
    """
    if usuario is None or not getattr(usuario, 'is_authenticated', False):
        return False
    return bool(getattr(usuario, 'activo', True))


def permisos_de_usuario(usuario, sucursal=None):
    """
    Set de codigos de permiso efectivos del usuario en el scope dado.

    Ver la nota de scope al inicio del modulo: `None` significa "solo
    asignaciones globales", NO "todas las sucursales".
    """
    if not _usuario_habilitado(usuario):
        return set()

    namespace = _namespace()
    clave = _cache_key(namespace, usuario.pk, sucursal)

    memo = _memo.get()
    if memo is not None and clave in memo:
        return memo[clave]

    if _cache_compartido():
        cacheado = cache.get(clave)
        if cacheado is not None:
            _memorizar(clave, cacheado)
            return cacheado

    if es_acceso_total(usuario):
        from .catalogo import codigos_catalogo
        codigos = set(codigos_catalogo())
    else:
        codigos = _resolver_permisos(usuario, sucursal)

    if _cache_compartido():
        cache.set(clave, codigos, CACHE_TIMEOUT)
    _memorizar(clave, codigos)
    return codigos


def _memorizar(clave, codigos):
    memo = _memo.get()
    if memo is None:
        memo = {}
        _memo.set(memo)
    memo[clave] = codigos


def _resolver_permisos(usuario, sucursal):
    """
    Permisos efectivos leidos de las asignaciones, con los filtros defensivos.

    Antes bastaba con que la fila existiera, estuviera activa y su rol tambien.
    No se comprobaba que el rol perteneciera al negocio del usuario, asi que una
    importacion, un comando, el admin o un bug de API podian crear una
    asignacion cruzada y el motor la convertia en privilegio efectivo
    (PER-004). Tampoco se miraba si el negocio o la sucursal seguian activos.
    """
    from django.db.models import Q

    from .models import AsignacionRol, Permiso

    negocio_id = getattr(usuario, 'negocio_id', None)
    if not negocio_id:
        # Un usuario sin negocio no tiene permisos de tenant. La alternativa
        # —honrar cualquier asignacion que alguien le haya colgado— es la mitad
        # de la escalada de PER-005.
        return set()

    asignaciones = AsignacionRol.objects.filter(
        usuario=usuario,
        activo=True,
        rol__activo=True,
        rol__negocio_id=negocio_id,
        rol__negocio__activo=True,
    )

    if sucursal is TODAS:
        pass  # union deliberada del negocio completo
    elif sucursal is None:
        asignaciones = asignaciones.filter(sucursal__isnull=True)
    else:
        sucursal_id = getattr(sucursal, 'pk', sucursal)
        asignaciones = asignaciones.filter(
            Q(sucursal__isnull=True)
            | Q(sucursal_id=sucursal_id, sucursal__activa=True)
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
    """
    True si el usuario tiene el permiso `codigo` en el scope dado.

    Dos limites se aplican POR ENCIMA del acceso total:

    1. **El codigo tiene que existir en el catalogo.** Antes `ADMIN` aprobaba
       cualquier string, incluido uno con typo: un gate nuevo mal escrito no
       protegia nada frente a un admin, y el error era invisible. Ahora un
       codigo desconocido deniega y deja rastro en el log.

    2. **Las capacidades del operador SaaS no son del admin de un tenant.** El
       catalogo describe `suscripciones.administrar` como capacidad del
       operador, pero el acceso total se la concedia a cualquier ADMIN — que en
       una BD tenant podia entonces editar su propia suscripcion.
    """
    from .catalogo import PERMISOS_OPERADOR_SAAS, codigos_catalogo

    if not _usuario_habilitado(usuario):
        return False

    if codigo not in codigos_catalogo():
        logger.warning(
            'Se consulto el permiso "%s", que no existe en el catalogo. '
            'Se deniega: revisar el gate que lo pide.', codigo,
        )
        return False

    if codigo in PERMISOS_OPERADOR_SAAS:
        return es_operador_global(usuario)

    if es_acceso_total(usuario):
        return True

    return codigo in permisos_de_usuario(usuario, sucursal)


def sucursales_con_permiso(usuario, codigo):
    """
    Sucursales donde `usuario` tiene el permiso `codigo`.

    Devuelve `None` cuando el alcance es GLOBAL —acceso total, o una asignacion
    sin sucursal— y un `set` de ids cuando esta acotado. `set()` vacio significa
    que no lo tiene en ninguna parte.

    `tiene_permiso(codigo)` responde "si, en este scope", que es lo que se
    necesita para un gate. Pero un reporte necesita ademas saber *donde*: sin
    esta distincion, un rol asignado solo a la sucursal A habilitaba consultas
    consolidadas que incluian las ventas de B.
    """
    if not _usuario_habilitado(usuario):
        return set()
    if es_acceso_total(usuario):
        return None

    negocio_id = getattr(usuario, 'negocio_id', None)
    if not negocio_id:
        return set()

    from .models import AsignacionRol

    asignaciones = AsignacionRol.objects.filter(
        usuario=usuario,
        activo=True,
        rol__activo=True,
        rol__negocio_id=negocio_id,
        rol__negocio__activo=True,
        rol__permisos__codigo=codigo,
    ).values_list('sucursal_id', flat=True).distinct()

    ids = set(asignaciones)
    if not ids:
        return set()
    if None in ids:
        # Una asignacion sin sucursal es deliberadamente global.
        return None
    return ids
