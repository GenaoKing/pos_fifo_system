from django.urls import path

from . import views

app_name = 'cuentas_por_cobrar'

urlpatterns = [
    path('', views.lista_cuentas, name='lista'),
    path('cliente/<int:cliente_id>/', views.estado_cuenta_cliente, name='cliente'),
    path('cliente/<int:cliente_id>/pdf/', views.estado_cuenta_cliente_pdf, name='cliente_pdf'),
    path('cliente/<int:cliente_id>/excel/', views.estado_cuenta_cliente_excel, name='cliente_excel'),
    path('api/metodos/', views.api_metodos_credito, name='api_metodos'),
    path('api/cliente/<int:cliente_id>/resumen/', views.api_resumen_cliente, name='api_resumen_cliente'),
    path('api/pago/', views.api_registrar_pago, name='api_registrar_pago'),
    path('api/pago/<int:pago_id>/anular/', views.api_anular_pago, name='api_anular_pago'),
    path('api/pago/<int:pago_id>/imprimir/', views.api_imprimir_recibo, name='api_imprimir_recibo'),
]
