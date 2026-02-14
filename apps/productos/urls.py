"""
URLs para la app de productos
"""

from django.urls import path
from . import views

app_name = 'productos'

urlpatterns = [
    # Productos
    path('', views.lista_productos, name='lista'),
    path('crear/', views.crear_producto, name='crear'),
    path('<int:producto_id>/editar/', views.editar_producto, name='editar'),
    path('<int:producto_id>/toggle-estado/', views.toggle_estado_producto, name='toggle_estado'),
    
    # Categorías
    path('categorias/', views.lista_categorias, name='categorias'),
    path('categorias/crear/', views.crear_categoria, name='crear_categoria'),
    path('categorias/<int:categoria_id>/editar/', views.editar_categoria, name='editar_categoria'),
    path('categorias/<int:categoria_id>/toggle-estado/', views.toggle_estado_categoria, name='toggle_estado_categoria'),

    #Codigo de barras
    path('<int:producto_id>/imprimir-etiqueta/', views.imprimir_etiqueta, name='imprimir_etiqueta'),
]
