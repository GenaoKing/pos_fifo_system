from django.apps import AppConfig


class TenancyConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.tenancy'
    verbose_name = 'Tenancy'

    def ready(self):
        # Registra los system checks de aislamiento (ver checks.py).
        from . import checks  # noqa: F401
