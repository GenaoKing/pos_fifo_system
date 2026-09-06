"""Adaptador reemplazable para Web Push.

El dominio solo conoce ``enviar`` y errores clasificados.  La dependencia de
``pywebpush`` y el formato VAPID quedan encerrados en este modulo.
"""
import json

from django.conf import settings
from requests import RequestException


class ErrorEntregaPush(Exception):
    def __init__(self, mensaje, *, status_code=None, reintentable=False):
        super().__init__(mensaje)
        self.status_code = status_code
        self.reintentable = reintentable


def habilitado_cliente():
    return bool(
        getattr(settings, 'WEB_PUSH_ENABLED', False)
        and getattr(settings, 'WEB_PUSH_VAPID_PUBLIC_KEY', '')
    )


def configurado():
    return bool(
        habilitado_cliente()
        and getattr(settings, 'WEB_PUSH_VAPID_PRIVATE_KEY', '')
    )


def clave_publica():
    return getattr(settings, 'WEB_PUSH_VAPID_PUBLIC_KEY', '') if habilitado_cliente() else ''


def _mensaje_seguro(error, status_code=None):
    """No persistir endpoint, claves, cuerpos ni montos del proveedor."""
    nombre = type(error).__name__
    status = status_code or getattr(error, 'status_code', None)
    return f'{nombre} HTTP {status}' if status else nombre


def enviar(suscripcion, destinatario):
    if not configurado():
        raise ErrorEntregaPush('Web Push no esta configurado.')

    # Import diferido: los POS locales no necesitan instalar la dependencia.
    from pywebpush import WebPushException, webpush

    evento = destinatario.evento
    ruta = evento.ruta or f'/notificaciones/{destinatario.pk}'
    payload = {
        'title': evento.titulo,
        'body': evento.cuerpo,
        'icon': '/icons/notificacion.svg',
        'badge': '/icons/notificacion.svg',
        'tag': f'notificacion-{destinatario.pk}',
        'url': ruta,
        'notificationId': destinatario.pk,
        'level': destinatario.nivel,
    }
    try:
        webpush(
            subscription_info={
                'endpoint': suscripcion.endpoint,
                'keys': {'p256dh': suscripcion.p256dh, 'auth': suscripcion.auth},
            },
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=settings.WEB_PUSH_VAPID_PRIVATE_KEY,
            vapid_claims={
                'sub': getattr(settings, 'WEB_PUSH_VAPID_SUBJECT', 'mailto:admin@example.com'),
            },
            ttl=getattr(settings, 'WEB_PUSH_TTL_SECONDS', 14400),
            timeout=getattr(settings, 'WEB_PUSH_TIMEOUT_SECONDS', 10),
        )
    except WebPushException as exc:
        # pywebpush conserva los errores HTTP en ``exc.response``. Algunas
        # versiones/wrappers exponen tambien ``status_code`` directamente.
        response = getattr(exc, 'response', None)
        status = getattr(response, 'status_code', None) or getattr(
            exc, 'status_code', None,
        )
        reintentable = status is None or status == 429 or status >= 500
        raise ErrorEntregaPush(
            _mensaje_seguro(exc, status),
            status_code=status,
            reintentable=reintentable,
        ) from exc
    except (RequestException, OSError, TimeoutError) as exc:
        raise ErrorEntregaPush(
            _mensaje_seguro(exc), reintentable=True,
        ) from exc
