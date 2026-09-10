"""Controles de sesion comunes al POS local."""
import time

from django.conf import settings
from django.contrib.auth import logout


SESSION_INICIO_ABSOLUTO = '_pos_session_started_at'


class SesionAbsolutaMiddleware:
    """Invalida una sesion al cumplir el maximo absoluto configurado."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ahora = time.time()
        if getattr(request, 'user', None) and request.user.is_authenticated:
            inicio = request.session.get(SESSION_INICIO_ABSOLUTO)
            maximo = int(getattr(settings, 'SESSION_ABSOLUTE_MAX_AGE', 43200))
            try:
                vencida = inicio is not None and ahora - float(inicio) >= maximo
            except (TypeError, ValueError):
                vencida = True
            if vencida:
                logout(request)
                request.session.flush()
                request.session_expirada_absoluta = True
            elif inicio is None:
                request.session[SESSION_INICIO_ABSOLUTO] = ahora

        response = self.get_response(request)

        # En el POST de login, AuthenticationMiddleware vio un anonimo al
        # entrar, pero `login()` reemplazo request.user dentro de la vista.
        if getattr(request, 'user', None) and request.user.is_authenticated:
            request.session.setdefault(SESSION_INICIO_ABSOLUTO, ahora)
        return response
