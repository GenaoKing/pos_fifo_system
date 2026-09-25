"""
apps/configuracion/context_processors.py
Inyecta la configuracion del negocio en TODOS los templates.

Agregar en settings.py -> TEMPLATES -> OPTIONS -> context_processors:
    'apps.configuracion.context_processors.config_negocio',
"""
from .utils import config_o_none, modulos_efectivos_o_vacio


def config_negocio(request):
    """
    Hace disponible en todos los templates:

    - `config`: datos del negocio (nombre, telefono, RNC, logo...), o `None`
      si todavia no existe. Uso:
        {{ config.nombre_negocio }} · {{ config.telefono }}

    - `modulos_efectivos`: set de keys de modulos ACTIVOS para la sucursal
      actual, resuelto por el mismo motor que gatea el backend. Los menus y
      pantallas deben preguntar por este set, NO por `config.modulo_*` (el flag
      crudo), que es la otra fuente de verdad que CFG-009/SUS-007 vino a unificar.
      Uso:
        {% if 'cotizaciones' in modulos_efectivos %} ... {% endif %}

    CFG-012: este processor corre en CADA render -- login, paginas de error,
    admin incluidos. Usa las variantes `_o_none()` / `_o_vacio()` a proposito:
    ni la ausencia de configuracion, ni un `SUCURSAL_CODIGO` que no resuelve,
    ni la falta de tenant activo (BUG-E) pueden tumbar una pagina mientras la
    instalacion termina de aprovisionarse. Las vistas que SI necesitan la
    config para decidir algo (medios de pago, e-CF, descuentos) siguen
    llamando `get_config()` directo, que sigue fallando fuerte a proposito.
    """
    return {
        'config': config_o_none(),
        'modulos_efectivos': modulos_efectivos_o_vacio(),
    }