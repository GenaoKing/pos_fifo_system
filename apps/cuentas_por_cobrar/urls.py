from django.urls import path

from . import views

app_name = 'cuentas_por_cobrar'

urlpatterns = [
    path('', views.lista_cuentas, name='lista'),
    path('cliente/<int:cliente_id>/', views.estado_cuenta_cliente, name='cliente'),
    path('api/metodos/', views.api_metodos_credito, name='api_metodos'),
    path('api/cliente/<int:cliente_id>/resumen/', views.api_resumen_cliente, name='api_resumen_cliente'),
    path('api/pago/', views.api_registrar_pago, name='api_registrar_pago'),
]
