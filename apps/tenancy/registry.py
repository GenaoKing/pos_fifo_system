from copy import deepcopy
from threading import RLock

from django.conf import settings
from django.db import connections
from django.db.utils import OperationalError, ProgrammingError

from .context import tenancy_enabled


_registry_lock = RLock()


def tenant_alias(tenant_key):
    return f'tnt_{tenant_key}'


def _base_db_config():
    config = deepcopy(settings.DATABASES['default'])
    config.setdefault('CONN_HEALTH_CHECKS', True)
    return config


def tenant_db_config(tenant):
    config = _base_db_config()
    config['NAME'] = tenant.db_name
    return config


def configure_tenant_database(tenant_or_key):
    from .models import Tenant

    if isinstance(tenant_or_key, Tenant):
        tenant = tenant_or_key
    else:
        tenant = Tenant.objects.using('default').get(
            tenant_key=str(tenant_or_key),
            activo=True,
        )

    alias = tenant_alias(tenant.tenant_key)
    config = tenant_db_config(tenant)
    with _registry_lock:
        if settings.DATABASES.get(alias) != config:
            settings.DATABASES[alias] = config
            connections.databases[alias] = config
    return tenant, alias


def configure_tenant_databases(silent=False):
    if not tenancy_enabled():
        return []

    try:
        from .models import Tenant

        tenants = list(Tenant.objects.using('default').filter(activo=True))
    except (OperationalError, ProgrammingError):
        if silent:
            return []
        raise

    configured = []
    for tenant in tenants:
        configured.append(configure_tenant_database(tenant))
    return configured


def tenant_from_alias(alias):
    if not alias.startswith('tnt_'):
        return None
    return alias[4:]
