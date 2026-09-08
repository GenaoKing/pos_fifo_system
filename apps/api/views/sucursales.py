"""
apps/api/views/sucursales.py
Estado de sincronización de sucursales para el portal cloud (Fase 5).

GET /api/v1/sucursales/status/
    Auth: JWT (EsAdminOSysadmin)
    Devuelve el estado de sync de todas las sucursales activas + un
    resumen agregado para los KPIs del header del dashboard.

Alimenta la sección "Estado de sucursales" del dashboard. Si en el
futuro necesitamos endpoints adicionales (status histórico, retry de
eventos desde el portal, etc.) viven bajo el mismo prefijo /sucursales/.
"""
from datetime import timedelta

from django.db.models import Count, Max
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import requiere_permiso
from apps.api.views.reportes import _estado_sync  # ver "Decisiones" abajo
from rest_framework import status

from apps.negocios.utils import resolver_negocio
from apps.sucursales.models import Sucursal
from apps.sync.models import EventoSync


# Umbral para señalar cola anormal de eventos pendientes.
PENDIENTES_UMBRAL_ALERTA = 50


def _construir_alerta(s_info):
    """Mensaje de alerta legible; None si no hay nada raro.

    Una sola alerta a la vez, en orden de prioridad. Si hay errores los
    mostramos primero porque son la situación más accionable.
    """
    if s_info['eventos_error'] > 0:
        return f"{s_info['eventos_error']} eventos con error sin resolver."
    if s_info['estado_sync'] == 'rojo':
        return 'Sucursal sin sincronizar hace más de 30 minutos.'
    if s_info['estado_sync'] == 'sin_datos':
        return 'Sucursal sin actividad de sync registrada.'
    if s_info['eventos_pendientes'] > PENDIENTES_UMBRAL_ALERTA:
        return (
            f"Cola de eventos pendientes inusualmente grande "
            f"({s_info['eventos_pendientes']})."
        )
    return None


@api_view(['GET'])
@permission_classes([IsAuthenticated, requiere_permiso('sucursales.ver')])
def sucursales_status(request):
    """Estado de sync de todas las sucursales activas."""
    ahora = timezone.now()
    hace_24h = ahora - timedelta(hours=24)

    # 1) Universo de sucursales activas del tenant, orden estable. Aislamiento
    # multi-negocio: un usuario con negocio solo ve sus sucursales; global/SYSADMIN
    # (negocio None) las ve todas y puede acotar con ?negocio=. Los counts de
    # EventoSync se scopean solos al filtrar por sucursal__codigo__in=codigos.
    resolucion = resolver_negocio(request)
    if not resolucion.permitido:
        return Response(
            {'error': resolucion.motivo or 'Sin acceso.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    sucursales_qs = resolucion.filtrar(
        Sucursal.objects.filter(activa=True).order_by('codigo')
    )
    sucursales = list(sucursales_qs)

    if not sucursales:
        return Response({
            'timestamp': ahora,
            'sucursales': [],
            'resumen': {
                'total_sucursales': 0,
                'verde': 0, 'amarillo': 0, 'rojo': 0, 'sin_datos': 0,
                'eventos_pendientes_total': 0,
                'eventos_error_total': 0,
            },
        })

    codigos = [s.codigo for s in sucursales]

    # 2) Counts (sucursal, estado) en una sola query agregada.
    eventos_por_estado = (
        EventoSync.objects
        .filter(sucursal__codigo__in=codigos)
        .values('sucursal__codigo', 'estado')
        .annotate(cnt=Count('id'))
    )
    stats = {cod: {} for cod in codigos}
    for row in eventos_por_estado:
        stats[row['sucursal__codigo']][row['estado']] = row['cnt']

    # 3) Throughput: eventos CONFIRMADOS en las últimas 24 horas.
    confirmados_24h_qs = (
        EventoSync.objects
        .filter(
            sucursal__codigo__in=codigos,
            estado='CONFIRMADO',
            confirmed_at__gte=hace_24h,
        )
        .values('sucursal__codigo')
        .annotate(cnt=Count('id'))
    )
    throughput_24h = {r['sucursal__codigo']: r['cnt'] for r in confirmados_24h_qs}

    # 4) Timestamp del último evento CONFIRMADO por sucursal.
    # Nota: este timestamp puede ser ligeramente distinto de Sucursal.ultima_sync
    # porque ultima_sync se toca al final del batch en recibir_eventos, mientras
    # que confirmed_at es por-evento. La diferencia es de milisegundos.
    ultimo_evento_qs = (
        EventoSync.objects
        .filter(sucursal__codigo__in=codigos, estado='CONFIRMADO')
        .values('sucursal__codigo')
        .annotate(ts=Max('confirmed_at'))
    )
    ultimo_ts = {r['sucursal__codigo']: r['ts'] for r in ultimo_evento_qs}

    # 5) Construir respuesta + resumen agregado.
    sucursales_list = []
    resumen = {
        'total_sucursales': len(sucursales),
        'verde': 0, 'amarillo': 0, 'rojo': 0, 'sin_datos': 0,
        'eventos_pendientes_total': 0,
        'eventos_error_total': 0,
    }

    for s in sucursales:
        s_stats = stats[s.codigo]
        pendientes = s_stats.get('PENDIENTE', 0)
        errores = s_stats.get('ERROR', 0)
        confirmados = s_stats.get('CONFIRMADO', 0)
        descartados = s_stats.get('DESCARTADO', 0)

        estado = _estado_sync(s.ultima_sync)

        minutos_desde = None
        if s.ultima_sync is not None:
            minutos_desde = round(
                (ahora - s.ultima_sync).total_seconds() / 60, 1
            )

        s_info = {
            'codigo': s.codigo,
            'nombre': s.nombre,
            'activa': s.activa,
            'ultima_sync': s.ultima_sync,
            'minutos_desde_ultima_sync': minutos_desde,
            'estado_sync': estado,
            'eventos_pendientes': pendientes,
            'eventos_error': errores,
            'eventos_confirmados_total': confirmados,
            'eventos_descartados': descartados,
            'eventos_confirmados_24h': throughput_24h.get(s.codigo, 0),
            'ultimo_evento_confirmado_en': ultimo_ts.get(s.codigo),
        }
        s_info['alerta'] = _construir_alerta(s_info)
        sucursales_list.append(s_info)

        resumen[estado] += 1
        resumen['eventos_pendientes_total'] += pendientes
        resumen['eventos_error_total'] += errores

    return Response({
        'timestamp': ahora,
        'sucursales': sucursales_list,
        'resumen': resumen,
    })