"""
apps/facturacion_electronica/urls.py

Rutas de la app. Por ahora solo el endpoint AJAX de estado de ECF.

Para incluir en el urlconf principal del proyecto, agregar al
config/urls.py:

    path(
        'facturacion-electronica/',
        include('apps.facturacion_electronica.urls'),
    ),
"""
from django.urls import path

from . import views

app_name = 'facturacion_electronica'

urlpatterns = [
    path(
        'api/ecf/estado/<int:venta_id>/',
        views.api_estado_ecf_venta,
        name='api_estado_ecf_venta',
    ),
]