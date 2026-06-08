"""
apps/api/permissions.py
Permisos personalizados para la API REST.

Niveles:
- EsAdminOSysadmin: para endpoints de reportes y gestion
- EsSoloLectura: para datos maestros (sucursales solo leen)
- EsSucursalAutenticada: para sync (requiere token de usuario_servicio
  de una sucursal activa)
"""
from rest_framework.permissions import BasePermission, IsAuthenticated


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


# =============================================================================
# RBAC granular (motor data-driven, multitenant) — apps/permisos
# =============================================================================
# Estas clases reemplazan el chequeo por rol hardcoded (EsAdminOSysadmin) por
# permisos concretos del catalogo (ej. 'clientes.crear'). El enforcement es
# server-side y default-deny; los frontends solo reflejan lo que el backend
# concede. Ver apps/permisos/engine.py.


class TienePermiso(BasePermission):
    """
    Concede acceso si el usuario tiene un permiso concreto del catalogo.

    El codigo se toma de `self.codigo` (via requiere_permiso(...)) o de
    `view.required_permission`. Si no hay codigo declarado, no bloquea
    (combinar siempre con IsAuthenticated).
    """
    codigo = None
    message = 'No tiene el permiso requerido para realizar esta accion.'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        codigo = self.codigo or getattr(view, 'required_permission', None)
        if not codigo:
            return True
        sucursal = getattr(request, 'sucursal', None)
        return user.tiene_permiso(codigo, sucursal=sucursal)


def requiere_permiso(codigo):
    """
    Factory: retorna una subclase de TienePermiso atada a `codigo`.

    Uso:
        permission_classes = [IsAuthenticated, requiere_permiso('clientes.crear')]
    """
    nombre = 'Requiere_' + codigo.replace('.', '_')
    return type(nombre, (TienePermiso,), {'codigo': codigo})


def _es_token_de_sucursal(request):
    """True si el request viene autenticado con un token de servicio de una
    sucursal activa (usado por el sync incremental)."""
    sucursal = getattr(request.auth, 'sucursal', None)
    return sucursal is not None and getattr(sucursal, 'activa', False)


class PuedeLeerMaestro(BasePermission):
    """
    Lectura de datos maestros. Permite:
      - token de servicio de sucursal activa (sync incremental), o
      - usuario con permiso '<view.permiso_base>.ver'.
    """
    message = 'No tiene permiso para consultar este recurso.'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if _es_token_de_sucursal(request):
            return True
        base = getattr(view, 'permiso_base', None)
        if not base:
            return True
        return user.tiene_permiso(
            f'{base}.ver', sucursal=getattr(request, 'sucursal', None)
        )


class MaestroPermisoMixin:
    """
    `get_permissions` para los ViewSets de datos maestros del portal.

    - Lecturas (list/retrieve): PuedeLeerMaestro (sync de sucursal o '<base>.ver').
    - Escrituras: permiso granular '<base>.{crear|editar|eliminar}'.

    El viewset debe declarar `permiso_base` (ej. 'clientes').
    """
    permiso_base = None
    _ACCION_SUFIJO = {
        'create': 'crear',
        'update': 'editar',
        'partial_update': 'editar',
        'destroy': 'eliminar',
    }

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [IsAuthenticated(), PuedeLeerMaestro()]
        sufijo = self._ACCION_SUFIJO.get(self.action)
        if sufijo and self.permiso_base:
            return [IsAuthenticated(), requiere_permiso(f'{self.permiso_base}.{sufijo}')()]
        # Fallback conservador para acciones no mapeadas.
        return [IsAuthenticated(), EsAdminOSysadmin()]


# =============================================================================
# Entitlement de modulos (suscripcion del tenant) — apps/suscripciones
# =============================================================================
# Capa ortogonal a los permisos: el permiso dice "este usuario PUEDE"; el modulo
# dice "el plan del negocio INCLUYE". Una funcion esta disponible sii ambas se
# cumplen. Combinar con requiere_permiso(...) en permission_classes.


class RequiereModulo(BasePermission):
    """
    Concede acceso solo si el modulo `modulo` esta activo para el negocio del
    usuario (su tenant). Fail-open si el usuario no tiene negocio resuelto
    (los modulos son comerciales, no de seguridad).
    """
    modulo = None
    message = 'Este modulo no esta incluido en el plan del negocio.'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        key = self.modulo or getattr(view, 'modulo_requerido', None)
        if not key:
            return True
        from apps.suscripciones.engine import modulo_activo
        negocio = getattr(user, 'negocio', None)
        return modulo_activo(key, negocio=negocio)


def requiere_modulo(key):
    """Factory: subclase de RequiereModulo atada a `key`.

    Uso: permission_classes = [IsAuthenticated, requiere_modulo('cuentas_por_cobrar'),
                               requiere_permiso('cuentas_por_cobrar.ver')]
    """
    nombre = 'RequiereModulo_' + key
    return type(nombre, (RequiereModulo,), {'modulo': key})