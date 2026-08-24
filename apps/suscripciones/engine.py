"""
apps/suscripciones/engine.py
Resolucion (con cache) de los modulos activos de un negocio/sucursal, y la regla
para poder desactivar un modulo.

API publica:
    modulos_negocio(negocio) -> set[str]
    modulos_activos(negocio, sucursal=None) -> set[str]
    modulo_activo(key, negocio=None, sucursal=None) -> bool
    puede_desactivarse(negocio, key) -> (bool, motivo)
    invalidar_cache()

Fail-open ante tenant indeterminado: si `negocio` es None, `modulo_activo` es
True (sin restriccion). Los entitlements son comerciales, no de seguridad; es
preferible NO romper el POS de un cliente por un negocio sin resolver. El control
de *seguridad* lo hacen los permisos (apps/permisos), que son default-deny.

Fail-open ante negocio SIN APROVISIONAR (BUG-D / docs/ARQUITECTURA_MODULOS.md):
un negocio que existe pero no tiene ni suscripcion activa con plan ni una sola
fila de `NegocioModulo` es, para efectos practicos, el mismo estado que
`negocio is None` -- nadie configuro entitlements todavia. Antes esto fallaba
CERRADO (solo core), que es la asimetria exacta que dejo el POS sin imprimir
en silencio: una sucursal sin negocio fallaba abierto, una CON negocio recien
creado (la ventana entre `bootstrap_negocio` y `bootstrap_suscripciones`)
fallaba cerrado, y las dos son indistinguibles para quien instala. En cuanto
exista una suscripcion con plan, o UNA fila de `NegocioModulo` (aunque sea
solo una exclusion), deja de aplicar: ya hay una decision explicita que
respetar.
"""
from django.core.cache import cache

from . import registry

CACHE_PREFIX = 'modulos_negocio'
CACHE_TIMEOUT = 300
_VERSION_KEY = 'modulos_version'


def _version():
    v = cache.get(_VERSION_KEY)
    if v is None:
        v = 1
        cache.set(_VERSION_KEY, v, None)
    return v


def invalidar_cache():
    try:
        cache.incr(_VERSION_KEY)
    except ValueError:
        cache.set(_VERSION_KEY, 2, None)


def _cache_key(negocio_id):
    return f'{CACHE_PREFIX}:v{_version()}:{negocio_id}'


def modulos_negocio(negocio):
    """Set efectivo de modulos del tenant (plan + overrides + cierre + core)."""
    if negocio is None:
        return set(registry.keys())  # fail-open

    key = _cache_key(negocio.pk)
    cached = cache.get(key)
    if cached is not None:
        return cached

    efectivo = _resolver_negocio(negocio)
    cache.set(key, efectivo, CACHE_TIMEOUT)
    return efectivo


def _resolver_negocio(negocio):
    from .models import NegocioModulo

    plan_keys = set()
    suscripcion = getattr(negocio, 'suscripcion', None)
    tiene_plan = suscripcion is not None and suscripcion.activa and bool(suscripcion.plan_id)
    if tiene_plan:
        plan_keys = set(suscripcion.plan.modulos.values_list('key', flat=True))

    overrides = list(NegocioModulo.objects.filter(negocio=negocio).select_related('modulo'))

    if not tiene_plan and not overrides:
        # Negocio sin aprovisionar (ver docstring del modulo): fail-open, como
        # el resto del sistema ante un tenant indeterminado.
        return set(registry.keys())

    incluidos = set()
    excluidos = set()
    for nm in overrides:
        (incluidos if nm.incluido else excluidos).add(nm.modulo.key)

    seleccion = (plan_keys | incluidos) - excluidos
    # Cierre de dependencias + core (siempre activo).
    return registry.cierre_dependencias(seleccion) | registry.core_keys()


def modulos_activos(negocio, sucursal=None):
    """Modulos del tenant menos los apagados localmente en la sucursal."""
    base = modulos_negocio(negocio)
    if sucursal is None:
        return base

    from .models import SucursalModuloOverride
    apagados = set(
        SucursalModuloOverride.objects
        .filter(sucursal=sucursal, activo=False)
        .values_list('modulo__key', flat=True)
    )
    apagados -= registry.core_keys()  # core no se puede apagar
    return base - apagados


def modulo_activo(key, negocio=None, sucursal=None):
    if negocio is None:
        return True  # fail-open (ver docstring del modulo)
    return key in modulos_activos(negocio, sucursal)


# ---------------------------------------------------------------------------
# Desactivacion: bloquear si hay dependientes activos o datos bloqueantes.
# ---------------------------------------------------------------------------

def _hay_cxc_abiertas(negocio):
    from apps.cuentas_por_cobrar.models import CuentaPorCobrar
    abiertas = CuentaPorCobrar.objects.filter(
        sucursal__negocio=negocio,
        estado__in=(
            CuentaPorCobrar.ESTADO_ABIERTA,
            CuentaPorCobrar.ESTADO_PARCIAL,
            CuentaPorCobrar.ESTADO_VENCIDA,
        ),
    ).exists()
    if abiertas:
        return 'Hay cuentas por cobrar abiertas en el negocio.'
    return None


def _hay_ecf_en_proceso(negocio):
    from apps.facturacion_electronica.interfaces import EstadosECF
    from apps.facturacion_electronica.models import ECF
    en_proceso = ECF.objects.filter(
        venta__sucursal__negocio=negocio,
        estado__in=EstadosECF.REINTENTABLES,  # PENDIENTE/ENVIADO/EN_PROCESO/ERROR
    ).exists()
    if en_proceso:
        return 'Hay comprobantes e-CF en proceso (pendientes de cierre con DGII).'
    return None


# Hooks de "datos bloqueantes" por modulo: impiden apagar un modulo que aun tiene
# datos en vuelo. Cada hook -> str (motivo) o None. Se llaman defensivamente
# (cualquier excepcion = None = no bloquea).
_HOOKS_DATOS = {
    'cuentas_por_cobrar': _hay_cxc_abiertas,
    'ecf': _hay_ecf_en_proceso,
}


def _datos_bloqueantes(negocio, key):
    hook = _HOOKS_DATOS.get(key)
    if hook is None:
        return None
    try:
        return hook(negocio)
    except Exception:
        return None


def puede_desactivarse(negocio, key):
    """
    (bool, motivo). Bloquea si:
      - hay modulos activos que dependen de `key`, o
      - hay datos bloqueantes (hook por modulo).
    """
    activos = modulos_negocio(negocio)
    dependientes_activos = registry.dependientes_de(key) & activos
    if dependientes_activos:
        return False, (
            f"No se puede desactivar '{key}': dependen de el "
            f"{', '.join(sorted(dependientes_activos))}."
        )

    motivo = _datos_bloqueantes(negocio, key)
    if motivo:
        return False, f"No se puede desactivar '{key}': {motivo}"

    return True, ''
