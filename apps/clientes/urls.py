from django.urls import path
from . import views

app_name = 'clientes'

urlpatterns = [
    path('', views.lista_clientes, name='lista'),
    path('crear/', views.crear_cliente, name='crear'),
    path('<int:cliente_id>/editar/', views.editar_cliente, name='editar'),
    path('<int:cliente_id>/toggle/', views.toggle_estado_cliente, name='toggle'),
    path('<int:cliente_id>/', views.detalle_cliente, name='detalle'),

    # API
    path('api/buscar/', views.buscar_clientes, name='api_buscar'),
]