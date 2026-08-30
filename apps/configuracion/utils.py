"""
apps/configuracion/utils.py
Helper para acceder a la configuracion en caliente con cache.
Importar desde cualquier parte del proyecto:
    from apps.configuracion.utils import get_config, modulo_activo

Esta configuracion decide si se puede vender sin inventario, que medios de pago
acepta caja, cuanto tiempo puede anularse una venta, que modulos aparecen y que
identidad fiscal se imprime. O sea: no es una preferencia de UI, es un control
plane. Tres hallazgos vivian en como se resolvia y se cacheaba.

--------------------------------------------------------------------------
La clave de cache lleva el tenant (CFG-001)
--------------------------------------------------------------------------
Era `config_negocio_<SUCURSAL_CODIGO>` y nada mas. Los codigos de sucursal son
LOCALES a cada base tenant, y `SD-001` es el habitual: dos negocios lo comparten
legitimamente. Se reprodujo que, tras cachear la config del tenant A, el tenant
B recibia la fila de A sin siquiera consultar su propia base. Nombre, RNC,
medios de pago, inventario negativo y flags fiscales de un negocio se aplicaban
al atender a otro — y como no se toca la base equivocada, el fallo no deja
rastro donde uno lo buscaria.

--------------------------------------------------------------------------
Una sucursal no resuelta NO recibe la primera fila (CFG-002)
--------------------------------------------------------------------------
Si `SUCURSAL_CODIGO` no resolvia, `get_sucursal_actual()` devolvia `None` y
`load(None)` retornaba `.objects.first()` — la configuracion de una sucursal
arbitraria, ademas cacheada bajo el codigo invalido. Un typo en el `.env` no
detenia la caja: la hacia operar con la identidad fiscal, los pagos y los
modulos de otra tienda.

Ahora: si hay codigo configurado, tiene que resolver. Si no resuelve, se levanta
`ConfiguracionNoResuelta`. El fallback legacy queda reservado a la instalacion
que deliberadamente no declara codigo.

--------------------------------------------------------------------------
El cache ya no es eterno (CFG-005)
--------------------------------------------------------------------------
Se guardaba con `timeout=None`. `save()` invalida solo en el proceso que
escribio, y ni `QuerySet.update()`, ni SQL directo, ni otro worker pasan por
ahi: se reprodujo que tras un `update()` a `pago_efectivo=False`, `get_config()`
siguio devolviendo `True` indefinidamente. Dos replicas podian discrepar sobre
si se aceptan pagos en efectivo, y reiniciar "curaba" el sintoma escondiendo la
causa.

Con un backend local el cache pasa a tener TTL corto; con uno compartido
(Redis) la invalidacion por `save()` si alcanza a todos y el TTL puede ser
largo.
"""
from django.conf import settings
from django.core.cache import cache

# TTL cuando el backend NO se comparte entre procesos: acota cuanto puede durar
# una discrepancia entre workers. No la elimina — eso lo hace un backend
# compartido— pero la convierte en segundos en vez de "hasta el proximo
# reinicio".
TTL_CACHE_LOCAL = 30

# Con backend compartido, `save()` invalida para todos: el TTL solo es una red
# de seguridad ante una escritura que no pase por el modelo.
TTL_CACHE_COMPARTIDO = 600

_BACKENDS_LOCALES = ('locmem', 'dummy')


class ConfiguracionNoResuelta(RuntimeError):
    """`SUCURSAL_CODIGO` esta configurado pero no corresponde a ninguna sucursal."""


def _cache_compartido():
    backend = (
        settings.CACHES.get('default', {}).get('BACKEND', '')
        if hasattr(settings, 'CACHES') else ''
    ).lower()
    return not any(local in backend for local in _BACKENDS_LOCALES)


def _namespace():
    """Identidad del tenant activo, o 'local' sin tenancy."""
    from apps.tenancy.context import (
        TenantContextError,
        get_current_tenant_key,
        tenancy_enabled,
    )

    if not tenancy_enabled():
        return 'local'

    key = get_current_tenant_key()
    if not key:
        # Fail-loud, igual que el router, el prefijo de media y el motor de
        # permisos: bajo tenancy, resolver configuracion sin tenant activo es un
        # bug de contexto. Degradar es lo que mezclaba negocios.
        raise TenantContextError(
            'Se pidio la configuracion sin tenant activo en contexto.'
        )
    return key


def cache_key_config(codigo_sucursal=None):
    """Clave de cache de la configuracion. Expuesta para poder invalidarla."""
    if codigo_sucursal is None:
        codigo_sucursal = getattr(settings, 'SUCURSAL_CODIGO', None)
    sufijo = codigo_sucursal or 'legacy'
    return f'config_negocio:{_namespace()}:{sufijo}'


def get_config():
    """
    Retorna la ConfiguracionNegocio cacheada para la sucursal actual.

    - Con `SUCURSAL_CODIGO`: la configuracion de ESA sucursal, o error si el
      codigo no resuelve.
    - Sin `SUCURSAL_CODIGO`: modo legacy, la unica configuracion de la base.
    """
    codigo_sucursal = getattr(settings, 'SUCURSAL_CODIGO', None)
    clave = cache_key_config(codigo_sucursal)

    config = cache.get(clave)
    if config is not None:
        return config

    from .models import ConfiguracionNegocio

    if codigo_sucursal:
        from apps.sucursales.models import get_sucursal_actual

        sucursal = get_sucursal_actual()
        if sucursal is None:
            config = _config_sin_sucursal(codigo_sucursal, ConfiguracionNegocio)
        else:
            config = ConfiguracionNegocio.load(sucursal=sucursal)
    else:
        config = ConfiguracionNegocio.load()

    ttl = TTL_CACHE_COMPARTIDO if _cache_compartido() else TTL_CACHE_LOCAL
    cache.set(clave, config, timeout=ttl)
    return config


def _config_sin_sucursal(codigo_sucursal, ConfiguracionNegocio):
    """
    Que hacer cuando `SUCURSAL_CODIGO` no resuelve.

    La regla no es "no resuelve => fallar" sino **"fallar donde hay algo que
    confundir"**, igual que en la resolucion de tenant (NEG-001):

    - `SUCURSAL_CODIGO` trae `SD-001` por DEFECTO en toda instalacion, asi que
      "el operador proporciono un codigo" no es cierto: puede ser simplemente
      que nadie lo toco. Con **una sola** configuracion en la base no hay otra
      tienda con la cual confundirse, y fallar dejaria sin arrancar una
      instalacion nueva o de una sola sucursal por un dato que no cambia nada.

    - Con **varias** configuraciones, devolver `.objects.first()` es exactamente
      el hallazgo: la caja opera con la identidad fiscal, los medios de pago y
      los modulos de una tienda arbitraria, y el error queda cacheado bajo el
      codigo invalido. Ahi si se falla.

    En ambos casos queda registro: un codigo que no resuelve siempre es un
    sintoma, aunque a veces sea inocuo.
    """
    import logging

    configuraciones = list(ConfiguracionNegocio.objects.all()[:2])

    if len(configuraciones) > 1:
        raise ConfiguracionNoResuelta(
            f'SUCURSAL_CODIGO="{codigo_sucursal}" no corresponde a ninguna '
            f'sucursal, y la base tiene varias configuraciones. Devolver una '
            f'arbitraria haria operar la caja con la identidad fiscal, los '
            f'medios de pago y los modulos de otra tienda.'
        )

    logging.getLogger('configuracion').warning(
        'SUCURSAL_CODIGO="%s" no resuelve a ninguna sucursal; se usa la unica '
        'configuracion disponible. Revisar deploy/env_cliente.env.',
        codigo_sucursal,
    )
    return ConfiguracionNegocio.load()


def config_de_sucursal(sucursal):
    """
    Configuracion de UNA sucursal concreta, sin mirar `SUCURSAL_CODIGO`.

    Existe por COM-001: los generadores de PDF resolvian el encabezado con
    `get_config()`, que sale de settings, aunque el objeto que documentan
    —cotizacion, estado de cuenta, cierre, factura— conoce su propia sucursal.
    Con `SUCURSAL_CODIGO=A`, una cotizacion de B se imprimia con el nombre, el
    RNC, la direccion, el telefono y el logo de A. En una disputa, el documento
    no representa de forma confiable quien lo emitio.

    Devuelve `None` si la sucursal no tiene configuracion propia; el llamador
    decide si cae al contexto global o si eso es un error.
    """
    if sucursal is None:
        return None

    from .models import ConfiguracionNegocio

    return ConfiguracionNegocio.objects.filter(sucursal=sucursal).first()


def config_para_documento(sucursal):
    """
    Configuracion con la que encabezar un documento de esa sucursal.

    Si la sucursal tiene la suya, esa. Si no —instalacion sin migrar, o
    documento consolidado sin sucursal— cae al contexto actual y deja
    constancia: un encabezado que no corresponde al hecho documentado es
    exactamente el hallazgo, y conviene que se note en el log antes de que se
    note en una factura.
    """
    import logging

    propia = config_de_sucursal(sucursal)
    if propia is not None:
        return propia

    if sucursal is not None:
        logging.getLogger('configuracion').warning(
            'La sucursal %s no tiene configuracion propia; el documento se '
            'encabeza con la configuracion del contexto actual.',
            getattr(sucursal, 'codigo', sucursal),
        )
    return get_config()


def invalidar_config(codigo_sucursal=None):
    """Descarta la configuracion cacheada de esta sucursal."""
    cache.delete(cache_key_config(codigo_sucursal))


# Nombres legacy (sufijo del flag de ConfiguracionNegocio) que difieren de la
# key del registro de modulos. Solo 'financiacion_coop' -> 'financiacion'.
_ALIAS_LEGACY = {'financiacion_coop': 'financiacion'}


def modulo_activo(nombre_modulo):
    """
    Verifica si un modulo esta activo. Acepta tanto los nombres legacy
    (sufijo del flag, ej. 'financiacion_coop') como las keys del registro
    (ej. 'financiacion').

    Resolucion:
      - Si se puede resolver el Negocio (tenant) de la sucursal actual ->
        usa el entitlement por tenant (apps/suscripciones/engine).
      - Si no (instalacion sin tenant provisionado) -> fallback al flag legacy
        de ConfiguracionNegocio, preservando la conducta anterior.
    """
    from apps.suscripciones import registry  # lazy: evita ciclos de import

    key = _ALIAS_LEGACY.get(nombre_modulo, nombre_modulo)

    sucursal = _sucursal_actual()
    negocio = getattr(sucursal, 'negocio', None) if sucursal is not None else None
    if negocio is not None:
        from apps.suscripciones.engine import modulo_activo as _modulo_activo_tenant
        return _modulo_activo_tenant(key, negocio=negocio, sucursal=sucursal)

    return _modulo_default_legacy(key)


def _sucursal_actual():
    from apps.sucursales.models import get_sucursal_actual
    return get_sucursal_actual()


def _modulo_default_legacy(key):
    """Conducta historica cuando no hay tenant resuelto:
      - core -> siempre activo.
      - con flag_legacy -> lee el flag de ConfiguracionNegocio.
      - vendible sin flag (ej. cuentas_por_cobrar) -> historicamente siempre on.
    """
    from apps.suscripciones import registry
    modulo = registry.modulo(key)
    if modulo is None:
        # Compatibilidad: nombre suelto sin entrada en el registro.
        return getattr(get_config(), f'modulo_{key}', False)
    if modulo.core:
        return True
    if modulo.flag_legacy:
        return getattr(get_config(), modulo.flag_legacy, False)
    return True


def get_metodos_pago():
    """Retorna lista de metodos de pago habilitados como strings"""
    return get_config().get_metodos_pago_activos()


def get_dato_negocio(campo):
    """
    Shortcut para obtener un dato del negocio.
    Uso: get_dato_negocio('nombre_negocio') -> 'Royal Plast'
    """
    return getattr(get_config(), campo, '')