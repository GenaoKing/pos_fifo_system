"""
apps/api/auth_views.py
Views de autenticación JWT para el portal administrativo cloud (Fase 5).

PortalTokenObtainPairView:
    Login del portal. Extiende el flujo default de SimpleJWT con:
    1. Chequeo explícito de Usuario.activo (porque el modelo usa 'activo'
       y no 'is_active'; el ModelBackend de Django no lo bloquea solo).
    2. Bloqueo de rol CAJERA (el portal cloud es admin-only).
    3. Claims custom en el JWT (username, rol, tenant_id placeholder).
    4. Bloque 'user' en la respuesta del login para hidratar el contexto
       del frontend sin un /me extra en el primer render.

perfil_actual:
    GET /me/ — el frontend lo llama tras un refresh o reload para
    reconstruir el contexto de sesión.
"""
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView


class PortalTokenObtainPairSerializer(TokenObtainPairSerializer):

    @classmethod
    def get_token(cls, user):
        """Claims que viajan dentro del JWT firmado."""
        token = super().get_token(user)
        token['username'] = user.username
        token['rol'] = getattr(user, 'rol', None)
        token['full_name'] = (
            user.get_full_name()
            if hasattr(user, 'get_full_name')
            else user.username
        )

        # TENANCY: hoy el cloud collector es single-tenant. Cuando entre
        # django-tenants, este claim llevará el slug del tenant y el
        # frontend lo usará para subdomain routing o header X-Tenant.
        token['tenant_id'] = None
        return token

    def validate(self, attrs):
        # super().validate() ejecuta authenticate() y popula self.user
        data = super().validate(attrs)

        # 1. Bloqueo explícito de usuarios inactivos.
        # El modelo Usuario usa 'activo' (no 'is_active'), así que el
        # ModelBackend de Django NO lo bloquea por default.
        if not getattr(self.user, 'activo', True):
            raise serializers.ValidationError(
                {'detail': 'Usuario inactivo. Contacte al administrador.'},
                code='usuario_inactivo',
            )

        # 2. Solo SYSADMIN y ADMIN entran al portal cloud.
        rol = getattr(self.user, 'rol', None)
        if rol not in ('SYSADMIN', 'ADMIN'):
            raise serializers.ValidationError(
                {'detail': 'Solo administradores pueden acceder al portal cloud.'},
                code='rol_no_autorizado',
            )

        # 3. Tocar último_acceso (best-effort; no crítico).
        if hasattr(self.user, 'ultimo_acceso'):
            self.user.ultimo_acceso = timezone.now()
            self.user.save(update_fields=['ultimo_acceso'])

        # 4. Adjuntar perfil para el primer render del portal.
        data['user'] = _user_payload(self.user)
        return data


class PortalTokenObtainPairView(TokenObtainPairView):
    """Login del portal cloud."""
    serializer_class = PortalTokenObtainPairSerializer


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def perfil_actual(request):
    """GET /api/v1/auth/me/ — perfil del usuario autenticado por JWT."""
    return Response(_user_payload(request.user))


def _user_payload(user):
    """Forma canónica del usuario para el frontend. Centralizada para
    que login y /me/ devuelvan exactamente la misma shape."""
    return {
        'id': user.id,
        'username': user.username,
        'email': getattr(user, 'email', ''),
        'first_name': getattr(user, 'first_name', ''),
        'last_name': getattr(user, 'last_name', ''),
        'full_name': (
            user.get_full_name()
            if hasattr(user, 'get_full_name')
            else user.username
        ),
        'rol': getattr(user, 'rol', None),
        'tenant_id': None,  # TENANCY
    }