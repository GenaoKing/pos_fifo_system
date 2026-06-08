"""
apps/configuracion/utils.py
Helper para acceder a la configuracion en caliente con cache.
Importar desde cualquier parte del proyecto:
    from apps.configuracion.utils import get_config, modulo_activo

FASE 2: get_config() ahora busca por sucursal actual.
Cache key: 'config_negocio_{codigo_sucursal}' o 'config_negocio' para legacy.
"""
from django.conf import settings
from django.core.cache import cache


def get_config():
    """
    Retorna la ConfiguracionNegocio cacheada para la sucursal actual.

    Flujo:
    1. Lee settings.SUCURSAL_CODIGO
    2. Si existe, busca config con FK a esa sucursal (cache key: config_negocio_{codigo})
    3. Si no existe, fallback legacy: carga la primera config (cache key: config_negocio)

    Se invalida automaticamente al guardar desde el modelo.
    Usa LocMemCache (in-process), perfecto para single-worker Waitress.
    """
    codigo_sucursal = getattr(settings, 'SUCURSAL_CODIGO', None)

    if codigo_sucursal:
        cache_key = f'config_negocio_{codigo_sucursal}'
    else:
        cache_key = 'config_negocio'

    config = cache.get(cache_key)
    if config is None:
        from .models import ConfiguracionNegocio

        if codigo_sucursal:
            # Fase 2: buscar por sucursal
            from apps.sucursales.models import get_sucursal_actual
            sucursal = get_sucursal_actual()
            config = ConfiguracionNegocio.load(sucursal=sucursal)
        else:
            # Legacy: sin sucursal configurada
            config = ConfiguracionNegocio.load()

        cache.set(cache_key, config, timeout=None)
    return config


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