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
    path('ventas-por-cajero/', reportes.ventas_por_cajero, name='api-reportes-ventas-por-cajero'),
    path('top-productos/', reportes.top_productos, name='api-reportes-top-productos'),
    path('cierre-consolidado/', reportes.cierre_consolidado, name='api-reportes-cierre-consolidado'),
    path('inventario-consolidado/', reportes.inventario_consolidado, name='api-reportes-inventario'),
]
