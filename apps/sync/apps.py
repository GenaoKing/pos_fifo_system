"""
apps/sync/apps.py
 
Configuracion de la app Sync.
 
Responsable de la sincronizacion entre la sucursal (donde corre este POS)
y la nube (API central con datos consolidados de todas las sucursales).
 
La app NO hace nada en modo standalone: si SYNC_ENABLED=False en settings,
los decoradores pasan y los eventos nunca se generan.
"""
from django.apps import AppConfig
 
 
class SyncConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.sync'
    verbose_name = 'Sincronizacion con la nube'
 