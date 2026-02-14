from django.urls import path
from . import views

app_name = 'cotizaciones'

urlpatterns = [
    path('', views.lista_cotizaciones, name='lista'),
    path('crear/', views.crear_cotizacion, name='crear'),
    path('<int:cotizacion_id>/', views.detalle_cotizacion, name='detalle'),

    # APIs
    path('api/guardar/', views.guardar_cotizacion, name='api_guardar'),
    path('api/<int:cotizacion_id>/datos/', views.obtener_datos_cotizacion, name='api_datos'),
    path('api/<int:cotizacion_id>/convertida/', views.marcar_convertida, name='api_convertida'),

    # PDF
    path('<int:cotizacion_id>/pdf/', views.descargar_pdf_cotizacion, name='descargar_pdf'),
]