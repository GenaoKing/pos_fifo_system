"""
apps/api/views/reportes_urls.py
URL patterns para endpoints de reportes consolidados.

Se incluyen desde urls.py principal:
    path('reportes/', include('apps.api.views.reportes_urls')),
"""

from django.urls import path
from . import reportes

urlpatterns = [
    path('ventas-hoy/', reportes.ventas_hoy, name='api-reportes-ventas-hoy'),
    path('ventas-hoy/<str:codigo_sucursal>/', reportes.ventas_hoy, name='api-reportes-ventas-sucursal'),
    path('comparativo/', reportes.comparativo_sucursales, name='api-reportes-comparativo'),
    path('inventario-consolidado/', reportes.inventario_consolidado, name='api-reportes-inventario'),
]