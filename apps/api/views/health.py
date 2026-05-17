"""
apps/api/views/health.py
Health check endpoint.

Usado por:
- Sucursales para verificar conectividad con el cloud
- Monitoreo para confirmar que la API está operativa
- SyncEngine.check_connection() en Fase 4

No requiere autenticación.
"""

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


API_VERSION = '1.0.0'


@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    """
    GET /api/v1/health/
    
    Retorna estado del servidor y timestamp.
    La sucursal usa este endpoint para:
    1. Verificar que hay conexión con el cloud
    2. Obtener la hora del servidor (para detectar desync de reloj)
    
    Response:
        {
            "status": "ok",
            "version": "1.0.0",
            "timestamp": "2026-04-17T14:30:00-04:00"
        }
    """
    return Response({
        'status': 'ok',
        'version': API_VERSION,
        'timestamp': timezone.now(),
    })