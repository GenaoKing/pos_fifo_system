"""
apps/sucursales/context_processors.py
Inyecta la sucursal actual en todos los templates.

Agregar en settings.py -> TEMPLATES -> OPTIONS -> context_processors:
    'apps.sucursales.context_processors.sucursal_actual',

Uso en template:
    {{ sucursal.codigo }}
    {{ sucursal.nombre }}
    {% if sucursal %}...{% endif %}
"""


def sucursal_actual(request):
    """
    Hace disponible 'sucursal' en todos los templates.
    Usa request.sucursal inyectado por SucursalMiddleware.
    """
    return {'sucursal': getattr(request, 'sucursal', None)}