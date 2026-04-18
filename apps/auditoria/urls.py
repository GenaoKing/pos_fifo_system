 
"""
URLs para el módulo de Auditoría
apps/auditoria/urls.py
"""
 
from django.urls import path
from . import views
 
app_name = 'auditoria'
 
urlpatterns = [
    path('', views.dashboard_auditoria, name='dashboard'),
    path('api/buscar/', views.api_auditoria_buscar, name='api_buscar'),
]
 