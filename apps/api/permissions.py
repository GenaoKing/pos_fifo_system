"""
apps/api/permissions.py
Permisos personalizados para la API REST.

Niveles:
- EsAdminOSysadmin: para endpoints de reportes y gestión
- EsSoloLectura: para datos maestros (sucursales solo leen)
- EsSucursalAutenticada: para sync (requiere Fase 2)
"""

from rest_framework.permissions import BasePermission


class EsAdminOSysadmin(BasePermission):
    """
    Permite acceso solo a usuarios con rol ADMIN o SYSADMIN.
    Usado para: reportes consolidados, gestión de datos maestros.
    """
    message = 'Se requiere rol de Administrador o Sysadmin.'

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return hasattr(request.user, 'rol') and request.user.rol in ('ADMIN', 'SYSADMIN')


class EsSoloLectura(BasePermission):
    """
    Permite solo métodos seguros (GET, HEAD, OPTIONS).
    Usado para: endpoints de datos maestros consumidos por sucursales.
    """
    message = 'Este endpoint es de solo lectura.'

    def has_permission(self, request, view):
        return request.method in ('GET', 'HEAD', 'OPTIONS')


class EsSucursalAutenticada(BasePermission):
    """
    Verifica que el request venga de una sucursal autenticada.
    
    TODO: FASE 2 — Implementar cuando exista SucursalTokenAuthentication.
    Por ahora permite cualquier usuario autenticado.
    """
    message = 'Se requiere autenticación de sucursal.'

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        # TODO: FASE 2 — Verificar que el token tenga sucursal asignada
        # return hasattr(request.auth, 'sucursal') and request.auth.sucursal is not None

        return True