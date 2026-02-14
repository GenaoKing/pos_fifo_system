from utils.impresoras.views import (
    ImprimirTicketAutomaticoView,
    ReimprimirTicketView,
    TestImpresoraView,
    HistorialImpresionesView,
    ListaVentasReimprimirView,
    EstadoImpresoraAPIView,
    UltimasImpresionesAPIView,)

from django.urls import path
app_name = 'impresion'


urlpatterns = [ path(
        'ticket/<int:venta_id>/',
        ImprimirTicketAutomaticoView.as_view(),
        name='imprimir_ticket'
    ),
    
    # Reimpresión (requiere permiso)
    path(
        'reimprimir/<int:venta_id>/',
        ReimprimirTicketView.as_view(),
        name='reimprimir_ticket'
    ),
    
    # Lista de ventas para reimprimir
    path(
        'reimprimir/',
        ListaVentasReimprimirView.as_view(),
        name='lista_reimprimir'
    ),
    
    # Test de impresora
    path(
        'test/',
        TestImpresoraView.as_view(),
        name='test_impresora'
    ),
    
    # Historial de impresiones
    path(
        'historial/',
        HistorialImpresionesView.as_view(),
        name='historial'
    ),
    
    # API Endpoints
    path(
        'api/estado/',
        EstadoImpresoraAPIView.as_view(),
        name='api_estado'
    ),
    path(
        'api/ultimas/',
        UltimasImpresionesAPIView.as_view(),
        name='api_ultimas'
    ),
    ]
