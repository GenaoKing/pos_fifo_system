"""
apps/api/views/health.py
Health check endpoint.

Usado por:
- Sucursales para verificar conectividad con el cloud
- Monitoreo para confirmar que la API esta operativa
- SyncEngine.check_connection() en Fase 4

No requiere autenticacion.
"""

from django.conf import settings
from django.db import connection
from django.utils import timezone
from rest_framework import status as http_status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


API_VERSION = '1.0.0'


@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    """
    GET /api/v1/health/

    Returns app status, DB status, version, commit and environment.
    """
    payload = {
        'status': 'ok',
        'db': 'ok',
        'version': getattr(settings, 'APP_VERSION', API_VERSION),
        'commit': getattr(settings, 'GIT_COMMIT_SHA', 'unknown'),
        'environment': getattr(settings, 'CLOUD_ENVIRONMENT', 'local'),
        'timestamp': timezone.now(),
    }

    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:
        payload['status'] = 'degraded'
        payload['db'] = 'error'
        return Response(payload, status=http_status.HTTP_503_SERVICE_UNAVAILABLE)

    return Response(payload)
