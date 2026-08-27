"""
apps/permisos/middleware.py

Limpia el memo de permisos al empezar y al terminar cada request.

Con un backend de cache local (`LocMemCache`, el default) el motor no cachea
permisos entre requests: cada worker resolveria una decision propia y una
revocacion tardaria hasta el TTL en llegarle a los otros dos procesos de
Gunicorn (PER-002). Lo que si cachea es dentro de UN request, porque los
decoradores, el filtro de plantilla y los servicios preguntan varias veces por
el mismo usuario.

Ese memo vive en un `ContextVar`, y los `ContextVar` sobreviven al request en un
worker sincrono: el hilo se reutiliza. Sin esta limpieza, el memo se convertiria
en el mismo cache entre requests que se quiso evitar.
"""
from .engine import limpiar_memo


class PermisosRequestCacheMiddleware:
    """Acota el memo de permisos a la vida de un request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        limpiar_memo()
        try:
            return self.get_response(request)
        finally:
            # Tambien al salir: un worker que reutiliza el hilo no debe empezar
            # el proximo request con decisiones del anterior, ni siquiera si
            # algo falla en el medio.
            limpiar_memo()
