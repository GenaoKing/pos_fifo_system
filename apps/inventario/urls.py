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

    # Editar una compra (corrección de errores de captura)
    path('compras/<int:compra_id>/editar/', views.compra_editar, name='compra_editar'),
    
    # API para buscar productos (usado en el formulario)
    path('api/productos/buscar/', views.productos_buscar, name='productos_buscar'),

    # Imprimir etiquetas de una compra
    path('compras/<int:compra_id>/imprimir-etiquetas/', views.compra_imprimir_etiquetas, name='compra_imprimir_etiquetas'),

    # Ajustes de inventario
    path('ajustes/', views.vista_ajustes, name='ajustes'),
    path('api/lotes/<int:producto_id>/', views.api_lotes_producto, name='api_lotes_producto'),
    path('api/ajustar/', views.api_ajustar_inventario, name='api_ajustar'),
]