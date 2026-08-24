"""
apps/sync/context_processors.py

Hace visible en el panel POS el resultado de la ultima conciliacion (Fase 3),
sin que quien atiende la caja tenga que abrir una consola.

Cacheado 60s: es una consulta por request si no, y la conciliacion misma solo
corre una vez al dia -- no hace falta mirarla en cada pageview.
"""
from django.conf import settings
from django.core.cache import cache

_CACHE_KEY = 'sync:estado_conciliacion'
_CACHE_TTL = 60


def estado_sync(request):
    """
    Expone `estado_conciliacion` en los templates:
        {% if estado_conciliacion.alerta %}...{% endif %}

    None si el sync no esta habilitado (instalacion standalone): no tiene
    sentido mostrar el estado de algo que no corre.
    """
    if not getattr(settings, 'SYNC_ENABLED', False):
        return {}

    estado = cache.get(_CACHE_KEY)
    if estado is None:
        estado = _calcular()
        cache.set(_CACHE_KEY, estado, _CACHE_TTL)

    return {'estado_conciliacion': estado}


def _calcular():
    from django.utils import timezone

    from apps.sync.models import LogSync

    ultimo = LogSync.objects.filter(tipo='CONCILIACION').order_by('-inicio').first()
    if ultimo is None:
        return {'alerta': False, 'motivo': None, 'ultimo': None}

    horas_desde = (timezone.now() - ultimo.inicio).total_seconds() / 3600
    atrasada = horas_desde > 48

    if ultimo.resultado == 'PARCIAL':
        n = len(ultimo.detalle or [])
        motivo = f'Conciliacion con {n} divergencia(s) detectada(s) el {timezone.localtime(ultimo.inicio):%Y-%m-%d}.'
    elif ultimo.resultado == 'FALLO':
        motivo = f'La conciliacion del {timezone.localtime(ultimo.inicio):%Y-%m-%d} no se pudo completar.'
    elif atrasada:
        motivo = f'Sin conciliacion hace {horas_desde:.0f} horas.'
    else:
        motivo = None

    return {
        'alerta': bool(motivo),
        'motivo': motivo,
        'ultimo': ultimo.inicio,
    }
