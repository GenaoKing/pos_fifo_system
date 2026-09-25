"""Politica comun de duracion absoluta para sesiones JWT."""

import time

from django.conf import settings


SESSION_STARTED_CLAIM = 'session_started_at'
SESSION_EXPIRES_CLAIM = 'session_expires_at'


class SesionAbsolutaExpirada(ValueError):
    pass


def _max_age():
    return int(getattr(settings, 'SESSION_ABSOLUTE_MAX_AGE', 12 * 60 * 60))


def agregar_limite_absoluto(token):
    """Congela el inicio; SimpleJWT copia estos claims al rotar el refresh."""
    inicio = int(token.get(SESSION_STARTED_CLAIM) or token.get('iat') or time.time())
    token[SESSION_STARTED_CLAIM] = inicio
    token[SESSION_EXPIRES_CLAIM] = inicio + _max_age()
    return token


def validar_limite_absoluto(token, *, ahora=None):
    """Rechaza una sesion al cumplir 12 h, aunque su JWT aun no haya vencido."""
    try:
        inicio = int(token.get(SESSION_STARTED_CLAIM) or token.get('iat'))
        declarado = int(token.get(SESSION_EXPIRES_CLAIM) or inicio + _max_age())
    except (TypeError, ValueError):
        raise SesionAbsolutaExpirada('Token sin inicio de sesion valido.')

    # Nunca se acepta que un claim amplie la politica vigente del servidor.
    limite = min(declarado, inicio + _max_age())
    instante = int(time.time() if ahora is None else ahora)
    if instante >= limite:
        raise SesionAbsolutaExpirada('La sesion alcanzo su maximo absoluto.')
    return limite
