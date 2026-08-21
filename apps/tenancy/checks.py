"""
apps/tenancy/checks.py

System checks de aislamiento. Corren en `manage.py check`, que el deploy ya
ejecuta, y en el arranque del runserver.

Existen porque la defensa principal de aislamiento —el router que falla en voz
alta para un modelo de tenant sin contexto— se puede desactivar con una sola
variable de entorno. Sin un check, esa variable apagaba la proteccion en
silencio y nada lo delataba hasta ver datos de un negocio en la base de otro.
"""
from django.conf import settings
from django.core.checks import Critical, Error, register


@register('tenancy')
def escape_no_permitido_en_cloud(app_configs, **kwargs):
    """
    `TENANCY_ALLOW_UNSCOPED_OPERATIONS` no puede estar encendida junto con
    DB-per-tenant.

    Con el escape abierto, el router deja de fallar ante un modelo de negocio
    sin tenant activo y devuelve `None`: Django resuelve a `default`, o sea el
    CONTROL PLANE. Cualquier codigo incompleto empieza a escribir filas
    operativas en la base equivocada, en silencio.

    Es una valvula de emergencia legitima en desarrollo. En una instalacion con
    tenancy activa es un fail-open.
    """
    if not getattr(settings, 'TENANCY_DB_PER_TENANT_ENABLED', False):
        return []
    if not getattr(settings, 'TENANCY_ALLOW_UNSCOPED_OPERATIONS', False):
        return []

    return [
        Critical(
            'TENANCY_ALLOW_UNSCOPED_OPERATIONS esta activa con '
            'TENANCY_DB_PER_TENANT_ENABLED.',
            hint=(
                'Con las dos encendidas, una consulta sin tenant activo cae al '
                'control plane en vez de fallar: se pueden crear filas de un '
                'negocio en la base equivocada. Apaga '
                'TENANCY_ALLOW_UNSCOPED_OPERATIONS en este ambiente.'
            ),
            id='tenancy.C001',
        )
    ]


@register('tenancy')
def tenants_con_namespace_de_media_valido(app_configs, **kwargs):
    """
    Ningun tenant activo puede quedar sin `media_prefix`.

    Un prefijo vacio degrada las rutas de media a globales aunque tenancy este
    encendido: los archivos de distintos negocios terminan en el mismo lugar
    del container compartido.
    """
    if not getattr(settings, 'TENANCY_DB_PER_TENANT_ENABLED', False):
        return []

    try:
        from .models import Tenant

        sin_prefijo = list(
            Tenant.objects.using('default')
            .filter(activo=True)
            .filter(media_prefix='')
            .values_list('tenant_key', flat=True)
        )
    except Exception:
        # Sin BD disponible (build de imagen, collectstatic) el check no aplica.
        return []

    if not sin_prefijo:
        return []

    return [
        Error(
            f'Tenants activos sin media_prefix: {sin_prefijo}.',
            hint='Asigna un namespace propio a cada tenant antes de operar.',
            id='tenancy.E001',
        )
    ]
