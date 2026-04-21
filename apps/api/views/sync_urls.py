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
    path('status/', sync.sync_status, name='api-sync-status'),
]