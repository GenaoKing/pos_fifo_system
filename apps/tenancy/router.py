from django.conf import settings

from .context import TenantContextError, get_current_tenant_alias, tenancy_enabled


CONTROL_PLANE_APPS = {'tenancy'}
# `token_blacklist` va al control plane: la sesion del portal es global a la
# identidad, no de un tenant. Si viviera por tenant, un logout no invalidaria
# el refresh y ademas habria que migrar la tabla en cada base.
DEFAULT_ONLY_APPS = {'admin', 'sessions', 'token_blacklist'}
DUAL_HOME_APPS = {'auth', 'contenttypes', 'usuarios', 'negocios'}


class TenantDatabaseRouter:
    """
    Routes control-plane models to default and tenant models to the active tenant DB.

    The router is dormant unless TENANCY_DB_PER_TENANT_ENABLED is true or code
    explicitly forces tenancy via apps.tenancy.context.force_tenancy().
    """

    def db_for_read(self, model, **hints):
        return self._db_for_model(model)

    def db_for_write(self, model, **hints):
        return self._db_for_model(model)

    def allow_relation(self, obj1, obj2, **hints):
        if not tenancy_enabled():
            return None
        db1 = obj1._state.db
        db2 = obj2._state.db
        if db1 and db2:
            return db1 == db2
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if not tenancy_enabled():
            return None

        if app_label in CONTROL_PLANE_APPS:
            return db == 'default'

        if db == 'default':
            return app_label in CONTROL_PLANE_APPS | DEFAULT_ONLY_APPS | DUAL_HOME_APPS

        if db.startswith('tnt_'):
            return app_label not in CONTROL_PLANE_APPS | DEFAULT_ONLY_APPS

        return None

    def _db_for_model(self, model):
        if not tenancy_enabled():
            return None

        app_label = model._meta.app_label
        if app_label in CONTROL_PLANE_APPS:
            return 'default'

        if app_label in DEFAULT_ONLY_APPS:
            return 'default'

        if app_label in DUAL_HOME_APPS:
            return get_current_tenant_alias() or 'default'

        alias = get_current_tenant_alias()
        if alias:
            return alias

        if getattr(settings, 'TENANCY_ALLOW_UNSCOPED_OPERATIONS', False):
            return None

        raise TenantContextError(
            f'Modelo tenant {model._meta.label} consultado sin tenant activo.'
        )
