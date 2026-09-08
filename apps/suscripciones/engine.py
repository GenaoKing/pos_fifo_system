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
from django.conf import settings
from django.core.cache import cache

from . import registry

CACHE_PREFIX = 'modulos_negocio'

# TTL corto cuando el backend NO se comparte entre procesos (SUS-003).
#
# Produccion arranca Gunicorn con tres workers y usa `LocMemCache`: las senales
# incrementan la version solo en el proceso que atendio la escritura, asi que
# tras suspender un negocio o quitarle un modulo, los otros dos workers podian
# conservarlo hasta 300 segundos. Un entitlement comercial revocado que sigue
# vivo cinco minutos en dos de cada tres requests no es una revocacion.
CACHE_TIMEOUT_LOCAL = 30
CACHE_TIMEOUT_COMPARTIDO = 300

_VERSION_KEY = 'modulos_version'
_BACKENDS_LOCALES = ('locmem', 'dummy')


def _cache_compartido():
    backend = (
        settings.CACHES.get('default', {}).get('BACKEND', '')
        if hasattr(settings, 'CACHES') else ''
    ).lower()
    return not any(local in backend for local in _BACKENDS_LOCALES)


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


def _namespace():
    """
    Identidad del tenant activo (SUS-002).

    La clave llevaba version global y `negocio_id`, nada mas. Bajo DB-per-tenant
    los PK son LOCALES a cada base: dos objetos de contextos distintos con
    `pk=1` resolvian una sola vez y el segundo recibia el set del primero. El
    motor de permisos ya usa `tenant_key` exactamente por esto.
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
        raise TenantContextError(
            'Se pidieron los modulos de un negocio sin tenant activo en contexto.'
        )
    return key


def _cache_key(negocio_id):
    return f'{CACHE_PREFIX}:v{_version()}:{_namespace()}:{negocio_id}'


def modulos_negocio(negocio):
    """Set efectivo de modulos del tenant (plan + overrides + cierre + core)."""
    if negocio is None:
        return set(registry.keys())  # fail-open

    key = _cache_key(negocio.pk)
    cached = cache.get(key)
    if cached is not None:
        return cached

    efectivo = _resolver_negocio(negocio)
    cache.set(
        key, efectivo,
        CACHE_TIMEOUT_COMPARTIDO if _cache_compartido() else CACHE_TIMEOUT_LOCAL,
    )
    return efectivo


# Estados de aprovisionamiento (SUS-001). Antes eran uno solo.
SIN_APROVISIONAR = 'SIN_APROVISIONAR'
SUSPENDIDA = 'SUSPENDIDA'
CON_PLAN = 'CON_PLAN'
CUSTOM = 'CUSTOM'


def estado_suscripcion(negocio, overrides=None):
    """
    En cual de los cuatro estados esta el negocio.

    El bug de SUS-001 es que `not tiene_plan and not overrides` mezclaba cosas
    con consecuencias opuestas:

      - "todavia nadie configuro este negocio" -> fail-open es una decision
        deliberada, para que una instalacion nueva no arranque sin funciones;
      - "lo suspendi", "le quite el plan", "borre el plan", "borre su ultimo
        override" -> son DECISIONES, y todas se leian como la primera.

    Se reprodujo: `activa=False` sin overrides devolvia TODAS las keys, y un
    PATCH `plan=null` tambien. La operacion administrativa que parece suspender
    hacia exactamente lo contrario.

    La regla: **si existe una fila de suscripcion, hubo una decision.** Solo la
    ausencia total —ni suscripcion ni overrides— es "sin aprovisionar".
    """
    from .models import NegocioModulo

    if overrides is None:
        overrides = list(
            NegocioModulo.objects.filter(negocio=negocio).select_related('modulo')
        )

    suscripcion = getattr(negocio, 'suscripcion', None)

    if suscripcion is None:
        return CUSTOM if overrides else SIN_APROVISIONAR

    if not suscripcion.activa:
        return SUSPENDIDA

    return CON_PLAN if suscripcion.plan_id else CUSTOM


def _resolver_negocio(negocio):
    from .models import NegocioModulo

    overrides = list(
        NegocioModulo.objects.filter(negocio=negocio).select_related('modulo')
    )
    estado = estado_suscripcion(negocio, overrides)

    if estado == SIN_APROVISIONAR:
        # Unico caso de contingencia: nadie decidio nada todavia (ver el
        # docstring del modulo). Una instalacion recien montada no puede quedar
        # sin funciones por un dato que aun no existe.
        return set(registry.keys())

    if estado == SUSPENDIDA:
        # Una suspension comercial NO puede aumentar capacidades. Queda lo
        # minimo con lo que el POS sigue siendo usable.
        return registry.core_keys()

    plan_keys = set()
    if estado == CON_PLAN:
        plan_keys = set(negocio.suscripcion.plan.modulos.values_list('key', flat=True))

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
    """
    True si `key` esta disponible para ese negocio (y esa sucursal).

    `negocio=None` sigue siendo fail-open —los modulos son comerciales, no de
    seguridad, y un tenant indeterminado no puede dejar sin POS a nadie— PERO
    solo cuando de verdad no hay a quien preguntarle. Si viene una sucursal, su
    negocio ES la respuesta: usarla en vez de rendirse (SUS-005).

    El caso concreto: un usuario de servicio con `negocio=NULL` y un token
    ligado a una sucursal cuyo plan no incluye CxC obtenia permiso igual, porque
    el gate solo miraba `user.negocio`.
    """
    if negocio is None and sucursal is not None:
        negocio = getattr(sucursal, 'negocio', None)

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
