"""
apps/api/sucursales_urls.py
Rutas relacionadas con sucursales para el portal cloud (Fase 5).
"""
from django.urls import path

from apps.api.views.sucursales import sucursales_status

urlpatterns = [
    path('status/', sucursales_status, name='api-sucursales-status'),
]