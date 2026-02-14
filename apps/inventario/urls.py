"""
URLs para el módulo de Inventario/Compras
apps/inventario/urls.py

Define las rutas para:
- Lista de compras
- Crear nueva compra
- Ver detalle de compra
- API de búsqueda de productos
"""

from django.urls import path
from . import views

app_name = 'inventario'

urlpatterns = [
    # Lista de compras
    path('compras/', views.compras_lista, name='compras_lista'),
    
    # Crear nueva compra
    path('compras/nueva/', views.compra_crear, name='compra_crear'),
    
    # Ver detalle de una compra específica
    path('compras/<int:compra_id>/', views.compra_detalle, name='compra_detalle'),
    
    # API para buscar productos (usado en el formulario)
    path('api/productos/buscar/', views.productos_buscar, name='productos_buscar'),
]