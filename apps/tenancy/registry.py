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


def configure_tenant_database(tenant_or_key, *, permitir_inactivo=False):
    """
    Registra el alias de BD de un tenant y devuelve (tenant, alias).

    `permitir_inactivo` es SOLO para aprovisionamiento: durante el bootstrap el
    tenant existe inactivo a proposito (no se publica hasta que su base este
    lista), y aun asi hay que poder conectarse para crearla y migrarla. Ningun
    camino de request debe usarlo.
    """
    from .models import Tenant

    if isinstance(tenant_or_key, Tenant):
        tenant = tenant_or_key
        # El chequeo de `activo` vivia solo en la rama que busca por key, asi
        # que pasar una instancia (lo que hacen varios comandos) saltaba el
        # control: un tenant dado de baja se configuraba igual.
        if not tenant.activo and not permitir_inactivo:
            raise Tenant.DoesNotExist(
                f'El tenant "{tenant.tenant_key}" esta inactivo.'
            )
    else:
        filtros = {'tenant_key': str(tenant_or_key)}
        if not permitir_inactivo:
            filtros['activo'] = True
        tenant = Tenant.objects.using('default').get(**filtros)

    alias = tenant_alias(tenant.tenant_key)
    config = tenant_db_config(tenant)
    with _registry_lock:
        if settings.DATABASES.get(alias) != config:
            settings.DATABASES[alias] = config
            connections.databases[alias] = config
            # Reemplazar el diccionario NO reemplaza la conexion: Django cachea
            # un `DatabaseWrapper` por alias en `connections`, y ese wrapper
            # conserva el NAME con el que se creo. Sin cerrarlo, un worker que
            # ya habia tocado el alias seguia escribiendo en la base anterior
            # mientras los procesos nuevos usaban la nueva: split-brain entre
            # dos bases del mismo tenant.
            _descartar_conexion(alias)
    return tenant, alias


def _descartar_conexion(alias):
    """Cierra y descarta el wrapper cacheado de un alias, si existe."""
    existente = getattr(connections, '_connections', None)
    if existente is None or not hasattr(existente, alias):
        return
    try:
        connections[alias].close()
    except Exception:  # pragma: no cover - cerrar es best-effort
        pass
    try:
        delattr(existente, alias)
    except AttributeError:  # pragma: no cover
        pass


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
