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
"""
from django import template

register = template.Library()


@register.filter(name='puede')
def puede(user, codigo):
    """True si `user` tiene el permiso `codigo` (delegado al motor RBAC)."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    tiene = getattr(user, 'tiene_permiso', None)
    if tiene is None:
        return False
    try:
        return bool(tiene(codigo))
    except Exception:
        # Nunca romper el render de una plantilla por un fallo del motor.
        return False
