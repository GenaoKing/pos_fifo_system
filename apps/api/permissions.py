"""
apps/api/permissions.py
Permisos personalizados para la API REST.

Niveles:
- EsAdminOSysadmin: para endpoints de reportes y gestion
- EsSoloLectura: para datos maestros (sucursales solo leen)
- EsSucursalAutenticada: para sync (requiere token de usuario_servicio
  de una sucursal activa)
"""
from rest_framework.permissions import BasePermission


class EsAdminOSysadmin(BasePermission):
    """
    Permite acceso solo a usuarios con rol ADMIN o SYSADMIN.
    Usado para: reportes consolidados, gestion de datos maestros.
    """
    message = 'Se requiere rol de Administrador o Sysadmin.'

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return hasattr(request.user, 'rol') and request.user.rol in ('ADMIN', 'SYSADMIN')


class EsSoloLectura(BasePermission):
    """
    Permite solo metodos seguros (GET, HEAD, OPTIONS).
    Usado para: endpoints de datos maestros consumidos por sucursales.
    """
    message = 'Este endpoint es de solo lectura.'

    def has_permission(self, request, view):
        return request.method in ('GET', 'HEAD', 'OPTIONS')


class EsSucursalAutenticada(BasePermission):
    """
    Verifica que el request venga de una sucursal autenticada via
    SucursalTokenAuthentication.

    Rechaza tokens de usuarios humanos (como Santiago admin) porque esos
    no tienen una sucursal asociada via usuario_servicio.
    """
    message = 'Este endpoint requiere un token de sucursal (no de usuario humano).'

    def has_permission(self, request, view):
        # Verifica autenticacion basica (user + token validos)
        if not request.user or not request.user.is_authenticated:
            return False
        if not request.auth:
            return False

        # Verifica que el token tenga sucursal inyectada por
        # SucursalTokenAuthentication y que esta sea activa
        sucursal = getattr(request.auth, 'sucursal', None)
        if sucursal is None:
            return False
        if not getattr(sucursal, 'activa', False):
            return False

        return True