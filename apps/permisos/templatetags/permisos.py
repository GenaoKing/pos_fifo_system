"""
apps/permisos/templatetags/permisos.py
Template tags/filtros para gatear UI del POS local por permiso.

Uso en plantillas:
    {% load permisos %}
    {% if request.user|puede:'compras.registrar' %}
        ... botón / sección ...
    {% endif %}

Reemplaza los checks por rol hardcoded ({% if request.user.rol == 'ADMIN' %}).
Default deny: usuario no autenticado o sin el permiso -> False.

El filtro resuelve la sucursal de la instalacion (`SUCURSAL_CODIGO`) y la pasa
al motor. Antes preguntaba sin scope, que en el motor significaba "unir todas
las sucursales": la UI mostraba botones de la sucursal A mientras se operaba la
B (PER-003). Estas plantillas solo corren en el POS local —el portal es React—,
asi que la sucursal de la instalacion es la respuesta correcta.
"""
import logging

from django import template

register = template.Library()
logger = logging.getLogger('permisos')


@register.filter(name='puede')
def puede(user, codigo):
    """True si `user` tiene el permiso `codigo` en la sucursal de esta instalacion."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    tiene = getattr(user, 'tiene_permiso', None)
    if tiene is None:
        return False
    try:
        return bool(tiene(codigo, sucursal=_sucursal_actual()))
    except Exception as exc:
        # Nunca romper el render de una plantilla por un fallo del motor.
        logger.warning(
            'No se pudo resolver el permiso %s al renderizar template: %s',
            codigo, type(exc).__name__,
        )
        return False


def _sucursal_actual():
    from apps.tenancy.context import tenancy_enabled

    if tenancy_enabled():
        return None

    from apps.sucursales.models import get_sucursal_actual

    return get_sucursal_actual()
