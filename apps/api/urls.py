"""
apps/api/urls.py
Router principal de la API REST.

Estructura:
    /api/v1/health/                    → Health check (público)
    /api/v1/maestros/productos/        → Datos maestros
    /api/v1/maestros/categorias/
    /api/v1/maestros/clientes/
    /api/v1/cuentas-por-cobrar/        → Cartera (read-only portal, B15)
    /api/v1/sync/eventos/              → Sync sucursal → cloud
    /api/v1/sync/status/
    /api/v1/reportes/ventas-hoy/       → Reportes consolidados
    /api/v1/reportes/comparativo/
    /api/v1/reportes/inventario-consolidado/
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views.maestros import ProductoViewSet, CategoriaViewSet, ClienteViewSet
from .views.cuentas_por_cobrar import CuentaPorCobrarViewSet
from .views.health import health_check

# ============================================
# ROUTER DRF — ViewSets de datos maestros
# ============================================

router_v1 = DefaultRouter()
router_v1.register(r'maestros/productos', ProductoViewSet, basename='producto')
router_v1.register(r'maestros/categorias', CategoriaViewSet, basename='categoria')
router_v1.register(r'maestros/clientes', ClienteViewSet, basename='cliente')
router_v1.register(
    r'cuentas-por-cobrar', CuentaPorCobrarViewSet, basename='cuenta-por-cobrar'
)

# ============================================
# URL PATTERNS
# ============================================

urlpatterns_v1 = [
    # Health check (sin autenticación)
    path('health/', health_check, name='api-health'),

    # Autenticación (tokens JWT)
    path('auth/', include('apps.api.auth_urls')),

    # Sucursales (Fase 5)
    path('sucursales/', include('apps.api.sucursales_urls')),

    # Datos maestros (router DRF)
    path('', include(router_v1.urls)),

    # Sync endpoints (placeholders funcionales — lógica completa en Fase 2)
    path('sync/', include('apps.api.views.sync_urls')),

    # Reportes endpoints (funcionales con datos locales — multi-sucursal en Fase 2)
    path('reportes/', include('apps.api.views.reportes_urls')),
]

# Versionado por URL: /api/v1/...
urlpatterns = [
    path('v1/', include((urlpatterns_v1, 'api-v1'))),
]