"""
apps/configuracion/context_processors.py
Inyecta la configuracion del negocio en TODOS los templates.

Agregar en settings.py -> TEMPLATES -> OPTIONS -> context_processors:
    'apps.configuracion.context_processors.config_negocio',
"""
from .utils import get_config


def config_negocio(request):
    """
    Hace disponible 'config' en todos los templates.
    Uso en template:
        {% if config.modulo_etiquetas_zebra %}
            <a href="...">Etiquetas</a>
        {% endif %}
        
        {{ config.nombre_negocio }}
        {{ config.telefono }}
    """
    return {'config': get_config()}