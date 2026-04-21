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

from django.db.models import Sum, Count, Avg, Q, DecimalField
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.ventas.models import Venta, Pago
from apps.productos.models import Producto

from ..permissions import EsAdminOSysadmin

logger = logging.getLogger('pos_system')


@api_view(['GET'])
@permission_classes([EsAdminOSysadmin])
def ventas_hoy(request, codigo_sucursal=None):
    """
    GET /api/v1/reportes/ventas-hoy/
    GET /api/v1/reportes/ventas-hoy/<codigo_sucursal>/
    
    Resumen de ventas del día actual.
    Sin código: retorna todas las sucursales.
    Con código: retorna solo esa sucursal.
    
    Response:
        {
            "fecha": "2026-04-17",
            "sucursales": [
                {
                    "sucursal_codigo": "SD-001",
                    "sucursal_nombre": "Royal Plast — Santo Domingo",
                    "cantidad_ventas": 45,
                    "total_ventas": "125430.00",
                    "total_descuentos": "3200.00",
                    "total_efectivo": "85000.00",
                    "total_transferencia": "35430.00",
                    "total_tarjeta": "5000.00",
                    "cantidad_anulaciones": 1,
                    "ultima_sync": "2026-04-17T14:30:00-04:00"
                }
            ],
            "totales": {
                "cantidad_ventas": 45,
                "total_ventas": "125430.00"
            }
        }
    """
    hoy = timezone.localdate()

    # Base query: ventas completadas de hoy
    ventas = Venta.objects.filter(
        fecha_venta__date=hoy,
        estado='COMPLETADA'
    )

    # TODO: FASE 2 — Filtrar por sucursal
    # if codigo_sucursal:
    #     ventas = ventas.filter(sucursal__codigo=codigo_sucursal)

    # Agregaciones
    totales = ventas.aggregate(
        cantidad=Count('id'),
        total=Coalesce(
            Sum('total'), Decimal('0.00'),
            output_field=DecimalField()
        ),
        descuentos=Coalesce(
            Sum('descuento_total'), Decimal('0.00'),
            output_field=DecimalField()
        ),
    )

    # Pagos por método
    pagos = Pago.objects.filter(
        venta__fecha_venta__date=hoy,
        venta__estado='COMPLETADA'
    )
    pagos_agg = pagos.values('metodo').annotate(
        total=Coalesce(
            Sum('monto'), Decimal('0.00'),
            output_field=DecimalField()
        )
    )
    pagos_dict = {p['metodo']: p['total'] for p in pagos_agg}

    # Anulaciones de hoy
    anulaciones = Venta.objects.filter(
        fecha_anulacion__date=hoy,
        estado='ANULADA'
    ).count()

    # TODO: FASE 2 — Agrupar por sucursal y construir lista
    # Por ahora retorna una sola "sucursal" (la local)
    sucursal_data = {
        'sucursal_codigo': 'LOCAL',
        'sucursal_nombre': 'Sucursal Principal',
        'cantidad_ventas': totales['cantidad'] or 0,
        'total_ventas': str(totales['total']),
        'total_descuentos': str(totales['descuentos']),
        'total_efectivo': str(pagos_dict.get('EFECTIVO', Decimal('0.00'))),
        'total_transferencia': str(pagos_dict.get('TRANSFERENCIA', Decimal('0.00'))),
        'total_tarjeta': str(pagos_dict.get('TARJETA', Decimal('0.00'))),
        'cantidad_anulaciones': anulaciones,
        'ultima_sync': None,
    }

    return Response({
        'fecha': str(hoy),
        'sucursales': [sucursal_data],
        'totales': {
            'cantidad_ventas': totales['cantidad'] or 0,
            'total_ventas': str(totales['total']),
        }
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