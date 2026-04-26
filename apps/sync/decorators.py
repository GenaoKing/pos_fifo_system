"""
apps/sync/decorators.py

Decorador @requiere_conexion_cloud.

Uso:
    from apps.sync.decorators import requiere_conexion_cloud

    @requiere_conexion_cloud(redirect_url='productos:lista')
    def editar_producto(request, producto_id):
        ...

Comportamiento:
- Si SYNC_ENABLED=False -> permite siempre (modo standalone, no hay cloud).
- Si SYNC_ENABLED=True y hay conexion -> permite, el view corre normal.
- Si SYNC_ENABLED=True y NO hay conexion:
    * Request normal: mensaje flash + redirect
    * Request AJAX (X-Requested-With: XMLHttpRequest) -> JsonResponse 503

NOTA ARQUITECTONICA:
Este decorador es un GUARD, no un proxy. Bloquea edicion offline, pero la
vista decorada sigue escribiendo en la BD LOCAL cuando esta online. La
semantica "las ediciones van directo al cloud y se propagan via pull" requiere
refactor de cada vista (escribir a la API en vez de al ORM local) y queda
como extension futura. Por ahora:

  - Sin decorador: la vista escribe local (legado).
  - Con decorador + online: la vista escribe local, el cambio no se propaga
    automaticamente a otras sucursales hasta que implementemos eventos de
    master data o el refactor a escritura directa al cloud.
  - Con decorador + offline: se bloquea.

El bloqueo offline SI es la proteccion critica que evita divergencia:
impide que dos sucursales editen el mismo producto sin saberlo.
"""
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect


def _is_ajax(request):
    """Heuristica para detectar requests AJAX (no hay ajax flag oficial en Django 4+)."""
    return (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or request.content_type == 'application/json'
        or request.path.startswith('/api/')
    )


def requiere_conexion_cloud(redirect_url='dashboard', mensaje=None):
    """
    Decorador que bloquea vistas de edicion de datos maestros cuando no hay
    conexion al cloud.

    Args:
        redirect_url: nombre de URL para redirect si offline (ej: 'dashboard')
        mensaje: texto custom a mostrar. Si None usa uno generico.
    """
    mensaje_default = (
        'Esta operacion requiere conexion a la nube. '
        'Los cambios administrativos no estan disponibles sin conexion.'
    )

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # Modo standalone: no hay cloud, el decorador es no-op
            if not getattr(settings, 'SYNC_ENABLED', False):
                return view_func(request, *args, **kwargs)

            # Importacion diferida para evitar cargar engine al importar decorators
            from .engine import SyncEngine

            engine = SyncEngine()
            if engine.check_connection():
                return view_func(request, *args, **kwargs)

            # OFFLINE: bloquear
            texto = mensaje or mensaje_default

            if _is_ajax(request):
                return JsonResponse({
                    'success': False,
                    'error': texto,
                    'codigo': 'OFFLINE_CLOUD',
                }, status=503)

            messages.error(request, texto)
            try:
                return redirect(redirect_url)
            except Exception:
                # Si el redirect_url no resuelve, al menos no tirar 500
                return redirect('/')

        return wrapper
    return decorator