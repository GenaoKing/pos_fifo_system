"""
apps/caja/urls.py
"""
from django.urls import path
from . import views

app_name = 'caja'

urlpatterns = [
    # Pagina principal
    path('', views.caja_index, name='index'),

    # APIs de turno
    path('api/abrir/', views.api_abrir_turno, name='api_abrir'),
    path('api/cerrar/', views.api_cerrar_turno, name='api_cerrar'),
    path('api/estado/', views.api_estado_turno, name='api_estado'),
    path('api/detalle/<int:turno_id>/', views.api_detalle_turno, name='api_detalle'),

    # Cuadre de caja imprimible: HTML para el navegador (Epson L4260 / tira 80mm)
    # y disparo a la impresora termica (ESC/POS) reutilizando print_manager.
    path('turno/<int:turno_id>/cuadre/', views.cuadre_ticket, name='cuadre_ticket'),
    path('api/turno/<int:turno_id>/cuadre/termica/', views.api_imprimir_cuadre_termica, name='api_cuadre_termica'),

    # API movimientos
    path('api/movimiento/', views.api_registrar_movimiento, name='api_movimiento'),

    # Soft-login admin
    path('api/validar-admin/', views.api_validar_admin, name='api_validar_admin'),

    # Historial (admin)
    path('historial/', views.historial_turnos, name='historial'),
]