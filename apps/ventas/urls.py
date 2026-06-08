"""
URLs para el módulo de Ventas/POS
apps/ventas/urls.py

Define las rutas para:
- Vista principal del POS
- APIs de búsqueda de productos
- Verificación de stock
- Procesamiento de ventas (Parte 3)
"""
from django.urls import path
from . import views

app_name = 'pos'

urlpatterns = [
    # Vista principal del POS
    path('', views.punto_venta, name='punto_venta'),

    # API: Buscar productos (autocompletado)
    path('api/buscar/', views.buscar_productos, name='buscar_productos'),

    # API: Accesos rapidos configurables
    path('api/accesos-rapidos/', views.accesos_rapidos_pos, name='accesos_rapidos_pos'),

    # API: Obtener producto por ID (accesos rapidos)
    path('api/producto-id/<int:producto_id>/', views.producto_por_id, name='producto_por_id'),

    # API: Obtener producto por codigo de barras
    path('api/producto/<str:codigo_barras>/', views.producto_por_codigo, name='producto_por_codigo'),

    # API: Verificar stock disponible
    path('api/stock/<int:producto_id>/', views.verificar_stock, name='verificar_stock'),

    # API para procesar venta
    path('api/procesar-venta/', views.procesar_venta, name='procesar_venta'),

    # Vista de confirmacion de venta
    path('venta/<int:venta_id>/exito/', views.venta_exitosa, name='venta_exitosa'),

    # Financiacion Cooperativa
    path('financiacion/', views.lista_financiaciones, name='lista'),
    path('financiacion/api/registrar/', views.registrar_financiacion, name='api_registrar'),
    path('financiacion/<int:venta_id>/', views.vista_financiacion, name='detalle'),
    path('financiacion/<int:venta_id>/pdf/', views.generar_pdf_financiacion, name='pdf'),

    # Anulaciones
    path('anulaciones/', views.vista_anulaciones, name='anulaciones'),
    path('api/anular-venta/', views.api_anular_venta, name='api_anular_venta'),
]
