"""
apps/api/views/sync_urls.py
URL patterns para endpoints de sincronización.

Se incluyen desde urls.py principal:
    path('sync/', include('apps.api.views.sync_urls')),
"""

from django.urls import path
from . import sync

urlpatterns = [
    path('eventos/', sync.recibir_eventos, name='api-sync-eventos'),
    path('heartbeat/', sync.heartbeat, name='api-sync-heartbeat'),
    path('status/', sync.sync_status, name='api-sync-status'),
    path('roles/', sync.roles_para_sucursal, name='api-sync-roles'),
    path('asignaciones/', sync.asignaciones_para_sucursal, name='api-sync-asignaciones'),
    path('metodos-credito/', sync.metodos_credito_para_sucursal, name='api-sync-metodos-credito'),
    path('configuracion/', sync.configuracion_para_sucursal, name='api-sync-configuracion'),
]
