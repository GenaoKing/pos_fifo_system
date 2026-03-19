"""
apps/reportes/urls.py
URLs del modulo de reportes y dashboard
"""
from django.urls import path
from . import views

app_name = 'reportes'

urlpatterns = [
    # Dashboard principal (redirige segun rol)
    path('', views.dashboard, name='dashboard'),

    # API tiempo real
    path('api/metricas-hoy/', views.api_metricas_hoy, name='api_metricas_hoy'),
]
