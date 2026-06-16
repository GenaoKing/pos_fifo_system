"""
apps/api/auth_urls.py
URLs de autenticación JWT para el portal administrativo cloud (Fase 5).

Aunque viven en la urlconf compartida entre todos los settings (local y
cloud), solo el cloud las consume. En sucursal estas rutas existen pero
no las llama nadie — la web Django local usa session auth.
"""
from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from .auth_views import PortalTokenObtainPairView, impersonar_tenant, perfil_actual

urlpatterns = [
    path('login/', PortalTokenObtainPairView.as_view(), name='api-auth-login'),
    path('impersonate/', impersonar_tenant, name='api-auth-impersonate'),
    path('refresh/', TokenRefreshView.as_view(), name='api-auth-refresh'),
    path('verify/', TokenVerifyView.as_view(), name='api-auth-verify'),
    path('me/', perfil_actual, name='api-auth-me'),
]
