"""
apps/reportes/urls.py
URLs del modulo de reportes: dashboard + reportes on-demand
"""
from django.urls import path
from . import views

app_name = 'reportes'

urlpatterns = [
    # Dashboard (ya existente)
    path('', views.dashboard, name='dashboard'),
    path('api/metricas-hoy/', views.api_metricas_hoy, name='api_metricas_hoy'),

    # Reportes On-Demand
    path('on-demand/', views.reportes_on_demand, name='on_demand'),

    # APIs para generar reportes
    path('api/cierre-manual/', views.api_cierre_manual, name='api_cierre_manual'),
    path('api/ventas-periodo/', views.api_ventas_periodo, name='api_ventas_periodo'),
    path('api/top-productos/', views.api_top_productos, name='api_top_productos'),
    path('api/inventario-valorizado/', views.api_inventario_valorizado, name='api_inventario_valorizado'),
    path('api/ventas-cajero/', views.api_ventas_cajero, name='api_ventas_cajero'),

    # Descarga PDF
    path('pdf/cierre/<int:cierre_id>/', views.descargar_pdf_cierre, name='descargar_pdf_cierre'),
]