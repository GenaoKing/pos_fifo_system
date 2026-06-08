from django.apps import AppConfig


class PermisosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.permisos'
    verbose_name = 'Permisos y Roles'

    def ready(self):
        # Conecta las signals de invalidacion de cache del motor de permisos.
        from . import signals  # noqa: F401
