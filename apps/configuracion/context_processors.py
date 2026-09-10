"""
apps/configuracion/context_processors.py
Inyecta la configuracion del negocio en TODOS los templates.

Agregar en settings.py -> TEMPLATES -> OPTIONS -> context_processors:
    'apps.configuracion.context_processors.config_negocio',
"""
from .utils import get_config, modulos_efectivos


def config_negocio(request):
    """
    Hace disponible en todos los templates:

    - `config`: datos del negocio (nombre, telefono, RNC, logo...). Uso:
        {{ config.nombre_negocio }} · {{ config.telefono }}

    - `modulos_efectivos`: set de keys de modulos ACTIVOS para la sucursal
      actual, resuelto por el mismo motor que gatea el backend. Los menus y
      pantallas deben preguntar por este set, NO por `config.modulo_*` (el flag
      crudo), que es la otra fuente de verdad que CFG-009/SUS-007 vino a unificar.
      Uso:
        {% if 'cotizaciones' in modulos_efectivos %} ... {% endif %}
    """
    return {
        'config': get_config(),
        'modulos_efectivos': modulos_efectivos(),
    }