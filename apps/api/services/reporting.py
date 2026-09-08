from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.cuentas_por_cobrar.models import PagoCxC
from apps.productos.models import Producto
from apps.sucursales.models import Sucursal
from apps.ventas.models import DetalleVenta, Pago, Venta


SYNC_VERDE_MAX_MINUTOS = 5
SYNC_AMARILLO_MAX_MINUTOS = 30
MAX_COMPARATIVO_PUNTOS = 400
MONEY_ZERO = Decimal('0.00')
MONEY_FIELD = DecimalField(max_digits=14, decimal_places=2)
METODO_TO_TOTAL_KEY = {
    'EFECTIVO': 'total_efectivo',
    'TRANSFERENCIA': 'total_transferencia',
    'TARJETA': 'total_tarjeta',
}


class ReportingError(Exception):
    def __init__(self, detail, status_code=400):
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


@dataclass(frozen=True)
class Periodo:
    desde: date
    hasta: date


def _estado_sync(ultima_sync):
    if ultima_sync is None:
        return 'sin_datos'

    delta = timezone.now() - ultima_sync
    if delta <= timedelta(minutes=SYNC_VERDE_MAX_MINUTOS):
        return 'verde'
    if delta <= timedelta(minutes=SYNC_AMARILLO_MAX_MINUTOS):
        return 'amarillo'
    return 'rojo'


def _money(value) -> str:
    value = value or MONEY_ZERO
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return str(value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def _decimal(value) -> Decimal:
    if value is None:
        return MONEY_ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _parse_date(value, field_name, default=None):
    if not value:
        return default
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError as exc:
        raise ReportingError(
            {'error': f'Parametro "{field_name}" debe tener formato YYYY-MM-DD.'},
            status_code=400,
        ) from exc


def _parse_period(params, default_today=True) -> Periodo:
    hoy = timezone.localdate()
    default = hoy if default_today else None
    desde = _parse_date(params.get('desde'), 'desde', default)
    hasta = _parse_date(params.get('hasta'), 'hasta', default)

    if desde is None or hasta is None:
        raise ReportingError(
            {'error': 'Debe enviar "desde" y "hasta" con formato YYYY-MM-DD.'},
            status_code=400,
        )
    if desde > hasta:
        raise ReportingError(
            {'error': '"desde" no puede ser mayor que "hasta".'},
            status_code=400,
        )
    return Periodo(desde=desde, hasta=hasta)


def _parse_single_date(params, name='fecha'):
    return _parse_date(params.get(name), name, timezone.localdate())


def _active_sucursales(codigo=None, resolucion=None):
    """Sucursales activas que alimentan los reportes consolidados.

    `resolucion` es el resultado tipado de `resolver_negocio()`. Antes llegaba
    un `Negocio | None` y `None` se leia como "todas las sucursales activas" —
    pero `None` tambien era lo que devolvia el resolver ante un usuario
    huerfano o un `?negocio=` inexistente. Los dos casos de FALLO terminaban en
    el scope mas amplio del sistema (NEG-001, NEG-002).

    `codigo` acota DENTRO del scope (un codigo de otro negocio devuelve 404).
    """
    queryset = Sucursal.objects.filter(activa=True).order_by('codigo')
    if resolucion is None:
        # Sin resolucion no hay a quien atribuirle el pedido: fail-closed.
        queryset = queryset.none()
    else:
        queryset = resolucion.filtrar(queryset)
    if codigo:
        queryset = queryset.filter(codigo=codigo)
        if not queryset.exists():
            raise ReportingError(
                {'error': f'Sucursal "{codigo}" no encontrada o inactiva.'},
                status_code=404,
            )
    return list(queryset)


def _period_response(periodo: Periodo, agrupacion=None):
    data = {'desde': str(periodo.desde), 'hasta': str(periodo.hasta)}
    if agrupacion:
        data['agrupacion'] = agrupacion
    return data


def _empty_metrics():
    return {
        'cantidad_ventas': 0,
        'ventas_facturadas': MONEY_ZERO,
        'credito_facturado': MONEY_ZERO,
        'cobros_cxc': MONEY_ZERO,
    }


def _empty_payment_metrics():
    return {
        'total_efectivo': MONEY_ZERO,
        'total_transferencia': MONEY_ZERO,
        'total_tarjeta': MONEY_ZERO,
    }


def _metrics_payload(metrics):
    cantidad = metrics.get('cantidad_ventas') or 0
    ventas_facturadas = _decimal(metrics.get('ventas_facturadas'))
    ticket = ventas_facturadas / Decimal(cantidad) if cantidad else MONEY_ZERO
    return {
        'cantidad_ventas': int(cantidad),
        'ventas_facturadas': _money(ventas_facturadas),
        'credito_facturado': _money(metrics.get('credito_facturado')),
        'cobros_cxc': _money(metrics.get('cobros_cxc')),
        'ticket_promedio': _money(ticket),
    }


def _legacy_ventas_omitidas(periodo: Periodo, estados=None):
    queryset = Venta.objects.filter(
        fecha_venta__date__gte=periodo.desde,
        fecha_venta__date__lte=periodo.hasta,
        sucursal__isnull=True,
    )
    if estados:
        queryset = queryset.filter(estado__in=estados)
    return queryset.count()


def _period_key(value, agrupacion):
    if hasattr(value, 'date'):
        value = timezone.localtime(value).date()
    if agrupacion == 'dia':
        return value.isoformat()
    if agrupacion == 'semana':
        inicio_semana = value - timedelta(days=value.weekday())
        return inicio_semana.isoformat()
    if agrupacion == 'mes':
        return value.strftime('%Y-%m')
    raise ReportingError(
        {'error': 'Parametro "agrupacion" debe ser dia, semana o mes.'},
        status_code=400,
    )


def _period_keys(periodo: Periodo, agrupacion):
    """Lista completa y ordenada de claves del rango, alineada con `_period_key`
    para que cada venta/pago del periodo caiga en una clave existente. Permite
    el zero-fill de la serie (un punto por periodo aunque no haya movimiento).

    Nota semana: la primera clave es el lunes ISO de `desde`, que puede ser
    anterior al rango (mismo comportamiento que `_period_key`).
    """
    keys = []
    if agrupacion == 'dia':
        current = periodo.desde
        while current <= periodo.hasta:
            keys.append(current.isoformat())
            current += timedelta(days=1)
    elif agrupacion == 'semana':
        current = periodo.desde - timedelta(days=periodo.desde.weekday())
        while current <= periodo.hasta:
            keys.append(current.isoformat())
            current += timedelta(days=7)
    else:  # mes — agrupacion ya validada por el caller
        current = periodo.desde.replace(day=1)
        while current <= periodo.hasta:
            keys.append(current.strftime('%Y-%m'))
            current = (current + timedelta(days=32)).replace(day=1)
    return keys


def _ventas_queryset(periodo: Periodo, codigos):
    return Venta.objects.filter(
        fecha_venta__date__gte=periodo.desde,
        fecha_venta__date__lte=periodo.hasta,
        estado='COMPLETADA',
        sucursal__codigo__in=codigos,
    ).select_related('sucursal', 'usuario')


def _pagos_queryset(periodo: Periodo, codigos):
    return Pago.objects.filter(
        venta__fecha_venta__date__gte=periodo.desde,
        venta__fecha_venta__date__lte=periodo.hasta,
        venta__estado='COMPLETADA',
        venta__sucursal__codigo__in=codigos,
    ).select_related('venta__sucursal')


def _pagos_cxc_queryset(periodo: Periodo, codigos):
    return PagoCxC.objects.filter(
        fecha_pago__date__gte=periodo.desde,
        fecha_pago__date__lte=periodo.hasta,
        estado=PagoCxC.ESTADO_APLICADO,
        cuenta__sucursal__codigo__in=codigos,
    ).select_related('cuenta__sucursal', 'registrado_por')


def _aggregate_sales_by_sucursal(periodo: Periodo, codigos):
    data = defaultdict(_empty_metrics)
    ventas_agg = (
        _ventas_queryset(periodo, codigos)
        .values('sucursal__codigo')
        .annotate(
            cantidad=Count('id'),
            ventas_facturadas=Coalesce(Sum('total'), Value(MONEY_ZERO), output_field=MONEY_FIELD),
            credito=Coalesce(
                Sum('total', filter=Q(condicion_pago='CREDITO')),
                Value(MONEY_ZERO),
                output_field=MONEY_FIELD,
            ),
        )
    )
    for row in ventas_agg:
        metrics = data[row['sucursal__codigo']]
        metrics['cantidad_ventas'] = row['cantidad']
        metrics['ventas_facturadas'] = row['ventas_facturadas']
        metrics['credito_facturado'] = row['credito']

    cxc_agg = (
        _pagos_cxc_queryset(periodo, codigos)
        .values('cuenta__sucursal__codigo')
        .annotate(total=Coalesce(Sum('monto'), Value(MONEY_ZERO), output_field=MONEY_FIELD))
    )
    for row in cxc_agg:
        data[row['cuenta__sucursal__codigo']]['cobros_cxc'] = row['total']
    return data


def build_ventas_hoy(params, codigo_sucursal=None, resolucion=None):
    hoy = timezone.localdate()
    periodo = Periodo(hoy, hoy)
    sucursales = _active_sucursales(codigo_sucursal, resolucion)
    codigos = [s.codigo for s in sucursales]
    metrics_by_sucursal = _aggregate_sales_by_sucursal(periodo, codigos)

    descuentos = {
        row['sucursal__codigo']: row['total']
        for row in _ventas_queryset(periodo, codigos)
        .values('sucursal__codigo')
        .annotate(total=Coalesce(Sum('descuento_total'), Value(MONEY_ZERO), output_field=MONEY_FIELD))
    }
    pagos = defaultdict(_empty_payment_metrics)
    for row in (
        _pagos_queryset(periodo, codigos)
        .exclude(metodo='CREDITO')
        .values('venta__sucursal__codigo', 'metodo')
        .annotate(total=Coalesce(Sum('monto'), Value(MONEY_ZERO), output_field=MONEY_FIELD))
    ):
        key = METODO_TO_TOTAL_KEY.get(row['metodo'])
        if key:
            pagos[row['venta__sucursal__codigo']][key] = row['total']

    anulaciones = {
        row['sucursal__codigo']: row['cantidad']
        for row in Venta.objects.filter(
            fecha_anulacion__date=hoy,
            estado='ANULADA',
            sucursal__codigo__in=codigos,
        )
        .values('sucursal__codigo')
        .annotate(cantidad=Count('id'))
    }

    sucursales_payload = []
    total_cantidad = 0
    total_ventas = MONEY_ZERO
    for sucursal in sucursales:
        metrics = metrics_by_sucursal[sucursal.codigo]
        payment_metrics = pagos[sucursal.codigo]
        total_cantidad += metrics['cantidad_ventas']
        total_ventas += _decimal(metrics['ventas_facturadas'])
        sucursales_payload.append({
            'sucursal_codigo': sucursal.codigo,
            'sucursal_nombre': sucursal.nombre,
            'cantidad_ventas': metrics['cantidad_ventas'],
            'total_ventas': _money(metrics['ventas_facturadas']),
            'ventas_facturadas': _money(metrics['ventas_facturadas']),
            'credito_facturado': _money(metrics['credito_facturado']),
            'cobros_cxc': _money(metrics['cobros_cxc']),
            'ticket_promedio': _metrics_payload(metrics)['ticket_promedio'],
            'total_descuentos': _money(descuentos.get(sucursal.codigo)),
            'total_efectivo': _money(payment_metrics['total_efectivo']),
            'total_transferencia': _money(payment_metrics['total_transferencia']),
            'total_tarjeta': _money(payment_metrics['total_tarjeta']),
            'cantidad_anulaciones': anulaciones.get(sucursal.codigo, 0),
            'ultima_sync': sucursal.ultima_sync,
            'estado_sync': _estado_sync(sucursal.ultima_sync),
        })

    return {
        'fecha': str(hoy),
        'sucursales': sucursales_payload,
        'totales': {
            'cantidad_ventas': total_cantidad,
            'total_ventas': _money(total_ventas),
            'ventas_facturadas': _money(total_ventas),
        },
        'metadata': {
            'legacy_ventas_omitidas': _legacy_ventas_omitidas(periodo, estados=['COMPLETADA']),
        },
    }


def build_comparativo(params, resolucion=None):
    periodo = _parse_period(params)
    agrupacion = params.get('agrupacion') or 'dia'
    if agrupacion not in ('dia', 'semana', 'mes'):
        raise ReportingError(
            {'error': 'Parametro "agrupacion" debe ser dia, semana o mes.'},
            status_code=400,
        )

    period_keys = _period_keys(periodo, agrupacion)
    if len(period_keys) > MAX_COMPARATIVO_PUNTOS:
        raise ReportingError(
            {
                'error': (
                    f'El rango genera {len(period_keys)} puntos '
                    f'(maximo {MAX_COMPARATIVO_PUNTOS}). Use una agrupacion '
                    'mayor (semana o mes) o un rango mas corto.'
                )
            },
            status_code=400,
        )

    sucursales = _active_sucursales(params.get('sucursal'), resolucion)
    codigos = [s.codigo for s in sucursales]
    totals = _aggregate_sales_by_sucursal(periodo, codigos)
    series = defaultdict(lambda: defaultdict(_empty_metrics))

    for venta in _ventas_queryset(periodo, codigos):
        cod = venta.sucursal.codigo
        key = _period_key(venta.fecha_venta, agrupacion)
        metrics = series[cod][key]
        metrics['cantidad_ventas'] += 1
        metrics['ventas_facturadas'] += venta.total
        if venta.condicion_pago == 'CREDITO':
            metrics['credito_facturado'] += venta.total

    for pago in _pagos_cxc_queryset(periodo, codigos):
        cod = pago.cuenta.sucursal.codigo
        key = _period_key(pago.fecha_pago, agrupacion)
        series[cod][key]['cobros_cxc'] += pago.monto

    sucursales_payload = []
    total_general = _empty_metrics()
    for sucursal in sucursales:
        metrics = totals[sucursal.codigo]
        total_general['cantidad_ventas'] += metrics['cantidad_ventas']
        total_general['ventas_facturadas'] += metrics['ventas_facturadas']
        total_general['credito_facturado'] += metrics['credito_facturado']
        total_general['cobros_cxc'] += metrics['cobros_cxc']
        sucursales_payload.append({
            'sucursal_codigo': sucursal.codigo,
            'sucursal_nombre': sucursal.nombre,
            'estado_sync': _estado_sync(sucursal.ultima_sync),
            'totales': _metrics_payload(metrics),
            'serie': [
                {
                    'periodo': key,
                    **_metrics_payload(
                        series[sucursal.codigo].get(key) or _empty_metrics()
                    ),
                }
                for key in period_keys
            ],
        })

    return {
        'periodo': _period_response(periodo, agrupacion),
        'sucursales': sucursales_payload,
        'totales': _metrics_payload(total_general),
        'metadata': {
            'legacy_ventas_omitidas': _legacy_ventas_omitidas(periodo, estados=['COMPLETADA']),
        },
    }


def build_ventas_por_cajero(params, resolucion=None):
    periodo = _parse_period(params)
    sucursales = _active_sucursales(params.get('sucursal'), resolucion)
    codigos = [s.codigo for s in sucursales]
    rows = defaultdict(lambda: {**_empty_metrics(), **_empty_payment_metrics(), 'usuario': None, 'sucursal': None})

    for venta in _ventas_queryset(periodo, codigos).select_related('usuario', 'sucursal'):
        key = (venta.usuario_id, venta.sucursal.codigo)
        row = rows[key]
        row['usuario'] = venta.usuario
        row['sucursal'] = venta.sucursal
        row['cantidad_ventas'] += 1
        row['ventas_facturadas'] += venta.total
        if venta.condicion_pago == 'CREDITO':
            row['credito_facturado'] += venta.total

    for pago in _pagos_queryset(periodo, codigos).exclude(metodo='CREDITO').select_related('venta__usuario', 'venta__sucursal'):
        key = (pago.venta.usuario_id, pago.venta.sucursal.codigo)
        row = rows[key]
        row['usuario'] = pago.venta.usuario
        row['sucursal'] = pago.venta.sucursal
        total_key = METODO_TO_TOTAL_KEY.get(pago.metodo)
        if total_key:
            row[total_key] += pago.monto

    for pago in _pagos_cxc_queryset(periodo, codigos).select_related('registrado_por', 'cuenta__sucursal'):
        key = (pago.registrado_por_id, pago.cuenta.sucursal.codigo)
        row = rows[key]
        row['usuario'] = pago.registrado_por
        row['sucursal'] = pago.cuenta.sucursal
        row['cobros_cxc'] += pago.monto

    cajeros = []
    for row in rows.values():
        usuario = row['usuario']
        sucursal = row['sucursal']
        # Defensa en profundidad: hoy Venta.usuario y PagoCxC.registrado_por son
        # NOT NULL (el sync cae al usuario_servicio si el cajero no existe en
        # cloud, ver apps/api/views/sync.py), pero si alguna vez llega un usuario
        # nulo lo representamos como "Sin usuario" en vez de reventar con
        # AttributeError (usuario.id) -> 500.
        if usuario is not None:
            usuario_id = usuario.id
            username = usuario.username
            nombre = usuario.get_full_name() or usuario.username
        else:
            usuario_id = None
            username = 'sin_usuario'
            nombre = 'Sin usuario'
        cajeros.append({
            'usuario_id': usuario_id,
            'username': username,
            'nombre': nombre,
            'sucursal_codigo': sucursal.codigo,
            'cantidad_ventas': row['cantidad_ventas'],
            'ventas_facturadas': _money(row['ventas_facturadas']),
            'total_efectivo': _money(row['total_efectivo']),
            'total_transferencia': _money(row['total_transferencia']),
            'total_tarjeta': _money(row['total_tarjeta']),
            'credito_facturado': _money(row['credito_facturado']),
            'cobros_cxc': _money(row['cobros_cxc']),
        })

    cajeros.sort(key=lambda item: (item['sucursal_codigo'], item['username']))
    return {
        'periodo': _period_response(periodo),
        'cajeros': cajeros,
        'metadata': {
            'legacy_ventas_omitidas': _legacy_ventas_omitidas(periodo, estados=['COMPLETADA']),
        },
    }


def build_top_productos(params, resolucion=None):
    periodo = _parse_period(params)
    sucursales = _active_sucursales(params.get('sucursal'), resolucion)
    codigos = [s.codigo for s in sucursales]
    try:
        limit = int(params.get('limit', 10))
    except (TypeError, ValueError) as exc:
        raise ReportingError({'error': '"limit" debe ser numerico.'}, status_code=400) from exc
    limit = max(1, min(limit, 100))

    productos = (
        DetalleVenta.objects
        .filter(
            venta__fecha_venta__date__gte=periodo.desde,
            venta__fecha_venta__date__lte=periodo.hasta,
            venta__estado='COMPLETADA',
            venta__sucursal__codigo__in=codigos,
        )
        .values('producto_id', 'producto__sku', 'producto__nombre', 'producto__categoria__nombre')
        .annotate(
            cantidad_vendida=Coalesce(Sum('cantidad'), Value(0)),
            ventas_facturadas=Coalesce(Sum('total_linea'), Value(MONEY_ZERO), output_field=MONEY_FIELD),
            transacciones=Count('venta_id', distinct=True),
        )
        .order_by('-ventas_facturadas', '-cantidad_vendida', 'producto__nombre')[:limit]
    )

    return {
        'periodo': _period_response(periodo),
        'productos': [
            {
                'producto_id': row['producto_id'],
                'sku': row['producto__sku'],
                'nombre': row['producto__nombre'],
                'categoria': row['producto__categoria__nombre'] or 'Sin categoria',
                'cantidad_vendida': _money(row['cantidad_vendida']),
                'ventas_facturadas': _money(row['ventas_facturadas']),
                'transacciones': row['transacciones'],
            }
            for row in productos
        ],
        'metadata': {
            'legacy_ventas_omitidas': _legacy_ventas_omitidas(periodo, estados=['COMPLETADA']),
        },
    }


def build_cierre_consolidado(params, resolucion=None):
    fecha = _parse_single_date(params)
    periodo = Periodo(fecha, fecha)
    sucursales = _active_sucursales(params.get('sucursal'), resolucion)
    codigos = [s.codigo for s in sucursales]
    totals = _aggregate_sales_by_sucursal(periodo, codigos)
    payments = defaultdict(_empty_payment_metrics)
    anulaciones = defaultdict(lambda: {'cantidad': 0, 'total': MONEY_ZERO})

    for row in (
        _pagos_queryset(periodo, codigos)
        .exclude(metodo='CREDITO')
        .values('venta__sucursal__codigo', 'metodo')
        .annotate(total=Coalesce(Sum('monto'), Value(MONEY_ZERO), output_field=MONEY_FIELD))
    ):
        key = METODO_TO_TOTAL_KEY.get(row['metodo'])
        if key:
            payments[row['venta__sucursal__codigo']][key] = row['total']

    for row in (
        Venta.objects
        .filter(fecha_anulacion__date=fecha, estado='ANULADA', sucursal__codigo__in=codigos)
        .values('sucursal__codigo')
        .annotate(
            cantidad=Count('id'),
            total=Coalesce(Sum('total'), Value(MONEY_ZERO), output_field=MONEY_FIELD),
        )
    ):
        anulaciones[row['sucursal__codigo']] = {'cantidad': row['cantidad'], 'total': row['total']}

    por_cajero = build_ventas_por_cajero({
        'desde': str(fecha),
        'hasta': str(fecha),
        **({'sucursal': params.get('sucursal')} if params.get('sucursal') else {}),
    }, resolucion=resolucion)['cajeros']
    por_cajero_by_sucursal = defaultdict(list)
    for row in por_cajero:
        por_cajero_by_sucursal[row['sucursal_codigo']].append(row)

    sucursales_payload = []
    total_general = {**_empty_metrics(), **_empty_payment_metrics(), 'cantidad_anulaciones': 0, 'total_anulaciones': MONEY_ZERO}
    for sucursal in sucursales:
        metrics = totals[sucursal.codigo]
        payment_metrics = payments[sucursal.codigo]
        anulacion = anulaciones[sucursal.codigo]
        total_general['cantidad_ventas'] += metrics['cantidad_ventas']
        total_general['ventas_facturadas'] += metrics['ventas_facturadas']
        total_general['credito_facturado'] += metrics['credito_facturado']
        total_general['cobros_cxc'] += metrics['cobros_cxc']
        total_general['total_efectivo'] += payment_metrics['total_efectivo']
        total_general['total_transferencia'] += payment_metrics['total_transferencia']
        total_general['total_tarjeta'] += payment_metrics['total_tarjeta']
        total_general['cantidad_anulaciones'] += anulacion['cantidad']
        total_general['total_anulaciones'] += anulacion['total']
        sucursales_payload.append({
            'sucursal_codigo': sucursal.codigo,
            'sucursal_nombre': sucursal.nombre,
            'cantidad_ventas': metrics['cantidad_ventas'],
            'ventas_facturadas': _money(metrics['ventas_facturadas']),
            'total_efectivo': _money(payment_metrics['total_efectivo']),
            'total_transferencia': _money(payment_metrics['total_transferencia']),
            'total_tarjeta': _money(payment_metrics['total_tarjeta']),
            'credito_facturado': _money(metrics['credito_facturado']),
            'cobros_cxc': _money(metrics['cobros_cxc']),
            'cantidad_anulaciones': anulacion['cantidad'],
            'total_anulaciones': _money(anulacion['total']),
            'por_cajero': por_cajero_by_sucursal[sucursal.codigo],
        })

    return {
        'fecha': str(fecha),
        'sucursales': sucursales_payload,
        'totales': {
            'cantidad_ventas': total_general['cantidad_ventas'],
            'ventas_facturadas': _money(total_general['ventas_facturadas']),
            'total_efectivo': _money(total_general['total_efectivo']),
            'total_transferencia': _money(total_general['total_transferencia']),
            'total_tarjeta': _money(total_general['total_tarjeta']),
            'credito_facturado': _money(total_general['credito_facturado']),
            'cobros_cxc': _money(total_general['cobros_cxc']),
            'cantidad_anulaciones': total_general['cantidad_anulaciones'],
            'total_anulaciones': _money(total_general['total_anulaciones']),
        },
        'metadata': {
            'legacy_ventas_omitidas': _legacy_ventas_omitidas(periodo),
        },
    }


def build_inventario_consolidado(params, resolucion=None):
    """Snapshot de inventario LOCAL (single-source), NO consolidado por sucursal.

    `resolucion` se acepta por uniformidad de la firma pero NO se usa: `Producto`
    no tiene FK a negocio, y su aislamiento es DB-per-tenant, no por queryset.
    Que este explicito evita que alguien lo agregue "por si acaso" sobre un
    campo que no existe.

    Hoy el stock vive en `Producto.stock_actual` (replicado del POS), no hay un
    modelo de stock por sucursal en cloud: por eso `stock_por_sucursal` trae una
    sola clave `LOCAL`. El payload marca esto explicito con `es_snapshot_local` y
    `fuente_stock` para que el portal no lo presente como dato multi-sucursal real.

    No se scopea por negocio: `Producto` no tiene FK negocio; su aislamiento es
    DB-per-tenant (una BD por tenant), no row-level.
    """
    productos = Producto.objects.select_related('categoria')

    activo = params.get('activo', 'true')
    if str(activo).lower() == 'true':
        productos = productos.filter(activo=True)

    categoria = params.get('categoria')
    if categoria:
        productos = productos.filter(categoria_id=categoria)

    bajo_stock_filter = params.get('bajo_stock')
    resultado = []
    bajo_stock_count = 0
    sin_stock_count = 0

    for producto in productos:
        stock = producto.stock_actual
        if stock <= 0:
            sin_stock_count += 1
        if stock <= producto.stock_minimo:
            bajo_stock_count += 1
        if bajo_stock_filter and str(bajo_stock_filter).lower() == 'true' and stock > producto.stock_minimo:
            continue
        resultado.append({
            'producto_id': producto.id,
            'producto_sku': producto.sku,
            'producto_nombre': producto.nombre,
            'categoria': producto.categoria.nombre if producto.categoria else 'Sin categoria',
            'stock_por_sucursal': {'LOCAL': stock},
            'stock_total': stock,
            'precio_venta': _money(producto.precio_venta),
            'necesita_reposicion': producto.necesita_reposicion,
        })

    return {
        'productos': resultado,
        'resumen': {
            'total_productos': len(resultado),
            'bajo_stock': bajo_stock_count,
            'sin_stock': sin_stock_count,
        },
        'ultima_actualizacion_por_sucursal': {},
        'sucursales_sin_datos': [],
        # Contrato explicito: snapshot de una sola fuente (LOCAL), no consolidado
        # por sucursal. El frontend no debe presentarlo como dato multi-sucursal.
        'es_snapshot_local': True,
        'fuente_stock': 'LOCAL',
    }
