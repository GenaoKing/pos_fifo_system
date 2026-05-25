"""
apps/api/views/reportes.py
Endpoints de reportes consolidados (cloud → dashboard React).

Estos endpoints alimentan el portal administrativo cloud (Fase 5)
con datos agregados de todas las sucursales.

TODO: FASE 2 — Requiere modelo Sucursal y que las ventas tengan
el FK sucursal poblado. La lógica de agregación está lista,
solo falta descomentar los filtros por sucursal.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from rest_framework import status   
from apps.sucursales.models import Sucursal    

from django.db.models import Sum, Count, Avg, Q, DecimalField
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.ventas.models import Venta, Pago
from apps.productos.models import Producto

from ..permissions import EsAdminOSysadmin

logger = logging.getLogger('pos_system')

# ============================================================================
# HELPERS
# ============================================================================

# Umbrales del semáforo de sync. Definidos aquí; si en algún momento queremos
# que sean configurables por ConfiguracionNegocio del cloud, los movemos.
SYNC_VERDE_MAX_MINUTOS = 5      # <= 5 min: verde (operación normal)
SYNC_AMARILLO_MAX_MINUTOS = 30  # entre 5 y 30 min: amarillo; después: rojo


def _estado_sync(ultima_sync):
    """
    Semáforo de sync a partir del timestamp de última sincronización recibida.

    Returns:
        'verde'     -> <= 5 min sin sync (estado normal)
        'amarillo'  -> entre 5 y 30 min (posible problema de red)
        'rojo'      -> > 30 min (intervención necesaria)
        'sin_datos' -> nunca sincronizó (sucursal nueva o el propio cloud)
    """
    if ultima_sync is None:
        return 'sin_datos'

    delta = timezone.now() - ultima_sync
    if delta <= timedelta(minutes=SYNC_VERDE_MAX_MINUTOS):
        return 'verde'
    if delta <= timedelta(minutes=SYNC_AMARILLO_MAX_MINUTOS):
        return 'amarillo'
    return 'rojo'


@api_view(['GET'])
@permission_classes([EsAdminOSysadmin])
def ventas_hoy(request, codigo_sucursal=None):
    """
    GET /api/v1/reportes/ventas-hoy/
    GET /api/v1/reportes/ventas-hoy/<codigo_sucursal>/

    Resumen de ventas del día actual por sucursal.

    Sin código: retorna TODAS las sucursales activas (incluyendo las que no
    han vendido hoy, con ceros). Esto permite que el dashboard muestre
    todas las sucursales aunque alguna esté inactiva en ventas pero activa
    operativamente.

    Con código: retorna solo esa sucursal (404 si no existe o no está activa).
    """
    hoy = timezone.localdate()

    # ---- 1) Universo de sucursales ----
    sucursales_qs = Sucursal.objects.filter(activa=True).order_by('codigo')
    if codigo_sucursal:
        sucursales_qs = sucursales_qs.filter(codigo=codigo_sucursal)
        if not sucursales_qs.exists():
            return Response(
                {'error': f'Sucursal "{codigo_sucursal}" no encontrada o inactiva.'},
                status=status.HTTP_404_NOT_FOUND,
            )

    # Inicializar diccionario codigo -> stats en ceros. Esto garantiza que
    # toda sucursal activa aparezca en la respuesta aunque no tenga ventas.
    sucursales_info = {
        s.codigo: {
            'sucursal_codigo': s.codigo,
            'sucursal_nombre': s.nombre,
            'cantidad_ventas': 0,
            'total_ventas': Decimal('0.00'),
            'total_descuentos': Decimal('0.00'),
            'total_efectivo': Decimal('0.00'),
            'total_transferencia': Decimal('0.00'),
            'total_tarjeta': Decimal('0.00'),
            'cantidad_anulaciones': 0,
            'ultima_sync': s.ultima_sync,
            'estado_sync': _estado_sync(s.ultima_sync),
        }
        for s in sucursales_qs
    }
    codigos = list(sucursales_info.keys())

    if not codigos:
        return Response({
            'fecha': str(hoy),
            'sucursales': [],
            'totales': {'cantidad_ventas': 0, 'total_ventas': '0.00'},
        })

    # ---- 2) Ventas COMPLETADAS de hoy, agrupadas por sucursal ----
    # Ventas legacy con sucursal=NULL quedan fuera por el filtro __in.
    ventas_agg = (
        Venta.objects
        .filter(
            fecha_venta__date=hoy,
            estado='COMPLETADA',
            sucursal__codigo__in=codigos,
        )
        .values('sucursal__codigo')
        .annotate(
            cantidad=Count('id'),
            total=Coalesce(
                Sum('total'), Decimal('0.00'), output_field=DecimalField()
            ),
            descuentos=Coalesce(
                Sum('descuento_total'), Decimal('0.00'), output_field=DecimalField()
            ),
        )
    )
    for row in ventas_agg:
        cod = row['sucursal__codigo']
        sucursales_info[cod]['cantidad_ventas'] = row['cantidad']
        sucursales_info[cod]['total_ventas'] = row['total']
        sucursales_info[cod]['total_descuentos'] = row['descuentos']

    # ---- 3) Pagos COMPLETADOS de hoy, por sucursal + método ----
    # Una venta MIXTA genera múltiples Pagos (uno por método), por eso la
    # agregación por (sucursal, metodo) refleja el desglose real.
    pagos_agg = (
        Pago.objects
        .filter(
            venta__fecha_venta__date=hoy,
            venta__estado='COMPLETADA',
            venta__sucursal__codigo__in=codigos,
        )
        .values('venta__sucursal__codigo', 'metodo')
        .annotate(
            total=Coalesce(
                Sum('monto'), Decimal('0.00'), output_field=DecimalField()
            ),
        )
    )
    METODO_TO_KEY = {
        'EFECTIVO': 'total_efectivo',
        'TRANSFERENCIA': 'total_transferencia',
        'TARJETA': 'total_tarjeta',
    }
    for row in pagos_agg:
        cod = row['venta__sucursal__codigo']
        key = METODO_TO_KEY.get(row['metodo'])
        if key:
            sucursales_info[cod][key] = row['total']

    # ---- 4) Anulaciones del día ----
    anulaciones_agg = (
        Venta.objects
        .filter(
            fecha_anulacion__date=hoy,
            estado='ANULADA',
            sucursal__codigo__in=codigos,
        )
        .values('sucursal__codigo')
        .annotate(cantidad=Count('id'))
    )
    for row in anulaciones_agg:
        cod = row['sucursal__codigo']
        sucursales_info[cod]['cantidad_anulaciones'] = row['cantidad']

    # ---- 5) Construir respuesta ----
    sucursales_list = []
    total_general_cantidad = 0
    total_general_ventas = Decimal('0.00')
    for cod in codigos:  # mantiene orden alfabético del queryset
        s = sucursales_info[cod]
        total_general_cantidad += s['cantidad_ventas']
        total_general_ventas += s['total_ventas']
        sucursales_list.append({
            **s,
            # Decimals -> str para serialización JSON estable
            'total_ventas': str(s['total_ventas']),
            'total_descuentos': str(s['total_descuentos']),
            'total_efectivo': str(s['total_efectivo']),
            'total_transferencia': str(s['total_transferencia']),
            'total_tarjeta': str(s['total_tarjeta']),
        })

    return Response({
        'fecha': str(hoy),
        'sucursales': sucursales_list,
        'totales': {
            'cantidad_ventas': total_general_cantidad,
            'total_ventas': str(total_general_ventas),
        },
    })


@api_view(['GET'])
@permission_classes([EsAdminOSysadmin])
def comparativo_sucursales(request):
    """
    GET /api/v1/reportes/comparativo/
    
    Comparativo de rendimiento entre sucursales.
    Incluye ventas de hoy, semana y mes, más estado de sync.
    
    Response:
        {
            "periodo": { "hoy": "2026-04-17", "inicio_semana": "...", "inicio_mes": "..." },
            "sucursales": [ ... ]
        }
    """
    hoy = timezone.localdate()
    inicio_semana = hoy - timedelta(days=hoy.weekday())
    inicio_mes = hoy.replace(day=1)

    # TODO: FASE 2 — Iterar sobre Sucursal.objects.filter(activa=True)
    # y agregar métricas por cada una

    # Placeholder: datos de la instancia local
    ventas_hoy_total = Venta.objects.filter(
        fecha_venta__date=hoy, estado='COMPLETADA'
    ).aggregate(
        total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField())
    )['total']

    ventas_semana_total = Venta.objects.filter(
        fecha_venta__date__gte=inicio_semana, estado='COMPLETADA'
    ).aggregate(
        total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField())
    )['total']

    ventas_mes_total = Venta.objects.filter(
        fecha_venta__date__gte=inicio_mes, estado='COMPLETADA'
    ).aggregate(
        total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField()),
        cantidad=Count('id'),
    )

    cantidad_mes = ventas_mes_total['cantidad'] or 1
    ticket_promedio = ventas_mes_total['total'] / cantidad_mes

    sucursal_data = {
        'sucursal_codigo': 'LOCAL',
        'sucursal_nombre': 'Sucursal Principal',
        'ventas_hoy': str(ventas_hoy_total),
        'ventas_semana': str(ventas_semana_total),
        'ventas_mes': str(ventas_mes_total['total']),
        'ticket_promedio': str(round(ticket_promedio, 2)),
        'productos_vendidos': Venta.objects.filter(
            fecha_venta__date__gte=inicio_mes, estado='COMPLETADA'
        ).aggregate(
            total=Coalesce(Sum('detalles__cantidad'), 0)
        )['total'],
        'estado_sync': 'verde',
    }

    return Response({
        'periodo': {
            'hoy': str(hoy),
            'inicio_semana': str(inicio_semana),
            'inicio_mes': str(inicio_mes),
        },
        'sucursales': [sucursal_data],
    })


@api_view(['GET'])
@permission_classes([EsAdminOSysadmin])
def inventario_consolidado(request):
    """
    GET /api/v1/reportes/inventario-consolidado/
    
    Stock consolidado de todos los productos a través de sucursales.
    
    Query params:
        ?categoria=<id>           → Filtrar por categoría
        ?bajo_stock=true          → Solo productos bajo stock mínimo
        ?activo=true              → Solo productos activos (default: true)
    
    Response:
        {
            "productos": [
                {
                    "producto_id": 1,
                    "producto_sku": "PROD-0001",
                    "producto_nombre": "Vaso 16oz",
                    "categoria": "Vasos",
                    "stock_por_sucursal": { "LOCAL": 150 },
                    "stock_total": 150,
                    "precio_venta": "15.00",
                    "necesita_reposicion": false
                }
            ],
            "resumen": {
                "total_productos": 120,
                "bajo_stock": 8,
                "sin_stock": 2
            }
        }
    """
    productos = Producto.objects.select_related('categoria')

    # Filtros
    activo = request.query_params.get('activo', 'true')
    if activo.lower() == 'true':
        productos = productos.filter(activo=True)

    categoria = request.query_params.get('categoria')
    if categoria:
        productos = productos.filter(categoria_id=categoria)

    bajo_stock_filter = request.query_params.get('bajo_stock')

    resultado = []
    bajo_stock_count = 0
    sin_stock_count = 0

    for producto in productos:
        stock = producto.stock_actual

        if stock <= 0:
            sin_stock_count += 1
        if stock <= producto.stock_minimo:
            bajo_stock_count += 1

        # Si el filtro de bajo stock está activo, saltar los que no aplican
        if bajo_stock_filter and bajo_stock_filter.lower() == 'true':
            if stock > producto.stock_minimo:
                continue

        # TODO: FASE 2 — stock_por_sucursal con query agrupado por sucursal
        resultado.append({
            'producto_id': producto.id,
            'producto_sku': producto.sku,
            'producto_nombre': producto.nombre,
            'categoria': producto.categoria.nombre if producto.categoria else 'Sin categoría',
            'stock_por_sucursal': {'LOCAL': stock},
            'stock_total': stock,
            'precio_venta': str(producto.precio_venta),
            'necesita_reposicion': producto.necesita_reposicion,
        })

    return Response({
        'productos': resultado,
        'resumen': {
            'total_productos': len(resultado),
            'bajo_stock': bajo_stock_count,
            'sin_stock': sin_stock_count,
        }
    })