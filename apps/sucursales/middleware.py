"""
apps/sucursales/middleware.py
Middleware que inyecta la sucursal actual en cada request.

Agrega request.sucursal con la instancia de Sucursal
basada en settings.SUCURSAL_CODIGO.

Agregar en settings.py MIDDLEWARE despues de AuthenticationMiddleware:
    'apps.sucursales.middleware.SucursalMiddleware',
"""
from .models import get_sucursal_actual
from apps.tenancy.context import tenancy_enabled


class SucursalMiddleware:
    """
    Inyecta request.sucursal en cada request.
    Si SUCURSAL_CODIGO no esta configurado o no existe, request.sucursal = None.
    Esto permite que el sistema siga funcionando sin sucursal (backward compatible).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if tenancy_enabled():
            request.sucursal = None
            return self.get_response(request)

        request.sucursal = get_sucursal_actual()
        response = self.get_response(request)
        return response
