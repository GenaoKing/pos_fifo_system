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


def modulo_activo(nombre_modulo):
    """
    Shortcut para verificar si un modulo esta activo.
    Uso: modulo_activo('etiquetas_zebra') -> True/False

    Nombres validos:
        etiquetas_zebra, financiacion_coop, cotizaciones,
        impresion_termica, barcode_scanner, reportes_ondemand,
        ecf, dashboard
    """
    return getattr(get_config(), f'modulo_{nombre_modulo}', False)


def get_metodos_pago():
    """Retorna lista de metodos de pago habilitados como strings"""
    return get_config().get_metodos_pago_activos()


def get_dato_negocio(campo):
    """
    Shortcut para obtener un dato del negocio.
    Uso: get_dato_negocio('nombre_negocio') -> 'Royal Plast'
    """
    return getattr(get_config(), campo, '')