"""
apps/configuracion/utils.py
Helper para acceder a la configuracion en caliente con cache.
Importar desde cualquier parte del proyecto:
    from apps.configuracion.utils import get_config, modulo_activo
"""
from django.core.cache import cache


def get_config():
    """
    Retorna la ConfiguracionNegocio cacheada.
    Se invalida automaticamente al guardar desde el modelo.
    Usa LocMemCache (in-process), perfecto para single-worker Waitress.
    """
    config = cache.get('config_negocio')
    if config is None:
        from .models import ConfiguracionNegocio
        config = ConfiguracionNegocio.load()
        cache.set('config_negocio', config, timeout=None)
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