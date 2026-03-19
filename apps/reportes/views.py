from django.shortcuts import render

# Create your views here.
"""
apps/reportes/views.py
Dashboard y vistas de reportes
"""
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Sum, Count, F, Q, Avg, DecimalField
from django.db.models.functions import Coalesce
from django.utils import timezone
from decimal import Decimal
from datetime import timedelta

from apps.ventas.models import Venta, DetalleVenta, Pago
from apps.productos.models import Producto, Categoria
from apps.inventario.models import Lote, Compra


# ============================================================================
# DASHBOARD PRINCIPAL
# ============================================================================

@login_required
def dashboard(request):
    """
    Dashboard principal - muestra version Admin o Cajera segun el rol
    """
    hoy = timezone.now().date()
    ahora = timezone.now()
    inicio_semana = hoy - timedelta(days=hoy.weekday())
    inicio_mes = hoy.replace(day=1)

    # ------------------------------------------------------------------
    # METRICAS COMUNES (ambos roles)
    # ------------------------------------------------------------------

    # Filtro base: ventas completadas de hoy
    ventas_hoy_qs = Venta.objects.filter(
        fecha_venta__date=hoy,
        estado='COMPLETADA'
    )

    # Para cajera: solo sus ventas
    if request.user.es_cajera:
        ventas_hoy_qs = ventas_hoy_qs.filter(usuario=request.user)

    resumen_hoy = ventas_hoy_qs.aggregate(
        total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField()),
        cantidad=Count('id'),
        descuentos=Coalesce(Sum('descuento_total'), Decimal('0.00'), output_field=DecimalField()),
    )

    # Desglose por metodo de pago (hoy)
    pagos_hoy = Pago.objects.filter(
        venta__in=ventas_hoy_qs
    ).values('metodo').annotate(
        total=Coalesce(Sum('monto'), Decimal('0.00'), output_field=DecimalField()),
        cantidad=Count('id'),
    ).order_by('metodo')

    pagos_dict = {p['metodo']: p for p in pagos_hoy}
    efectivo_hoy = pagos_dict.get('EFECTIVO', {}).get('total', Decimal('0.00'))
    transferencia_hoy = pagos_dict.get('TRANSFERENCIA', {}).get('total', Decimal('0.00'))
    tarjeta_hoy = pagos_dict.get('TARJETA', {}).get('total', Decimal('0.00'))

    # Ultimas ventas
    ultimas_ventas = Venta.objects.filter(
        estado='COMPLETADA'
    ).select_related('usuario').order_by('-fecha_venta')

    if request.user.es_cajera:
        ultimas_ventas = ultimas_ventas.filter(usuario=request.user)

    ultimas_ventas = ultimas_ventas[:10]

    # ------------------------------------------------------------------
    # METRICAS SOLO ADMIN
    # ------------------------------------------------------------------
    context_admin = {}

    if request.user.es_admin:
        # Ventas de la semana
        ventas_semana_qs = Venta.objects.filter(
            fecha_venta__date__gte=inicio_semana,
            estado='COMPLETADA'
        )
        resumen_semana = ventas_semana_qs.aggregate(
            total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField()),
            cantidad=Count('id'),
        )

        # Ventas del mes
        ventas_mes_qs = Venta.objects.filter(
            fecha_venta__date__gte=inicio_mes,
            estado='COMPLETADA'
        )
        resumen_mes = ventas_mes_qs.aggregate(
            total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField()),
            cantidad=Count('id'),
        )

        # Comparativa con ayer
        ayer = hoy - timedelta(days=1)
        total_ayer = Venta.objects.filter(
            fecha_venta__date=ayer,
            estado='COMPLETADA'
        ).aggregate(
            total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField()),
        )['total']

        if total_ayer > 0:
            variacion_diaria = ((resumen_hoy['total'] - total_ayer) / total_ayer) * 100
        else:
            variacion_diaria = Decimal('0.00')

        # Top 5 productos vendidos (mes actual)
        top_productos = DetalleVenta.objects.filter(
            venta__fecha_venta__date__gte=inicio_mes,
            venta__estado='COMPLETADA'
        ).values(
            'producto__nombre', 'producto__sku'
        ).annotate(
            total_vendido=Sum('cantidad'),
            total_monto=Sum('total_linea'),
        ).order_by('-total_vendido')[:5]

        # Productos con stock bajo
        productos_bajo_stock = []
        productos_activos = Producto.objects.filter(activo=True, stock_minimo__gt=0)
        for prod in productos_activos:
            stock_actual = Lote.objects.filter(
                producto=prod,
                cantidad_actual__gt=0,
                activo=True
            ).aggregate(
                total=Coalesce(Sum('cantidad_actual'), 0)
            )['total']
            if stock_actual <= prod.stock_minimo:
                productos_bajo_stock.append({
                    'producto': prod,
                    'stock_actual': stock_actual,
                    'stock_minimo': prod.stock_minimo,
                    'porcentaje': int((stock_actual / prod.stock_minimo) * 100) if prod.stock_minimo > 0 else 0,
                })
        productos_bajo_stock.sort(key=lambda x: x['porcentaje'])

        # Inventario valorizado total
        lotes_activos = Lote.objects.filter(
            cantidad_actual__gt=0, activo=True
        )
        inventario_total = lotes_activos.aggregate(
            valor=Coalesce(
                Sum(F('cantidad_actual') * F('costo_unitario')),
                Decimal('0.00'),
                output_field=DecimalField()
            ),
            items=Coalesce(Sum('cantidad_actual'), 0),
        )

        # Anulaciones de hoy
        anulaciones_hoy = Venta.objects.filter(
            fecha_anulacion__date=hoy,
            estado='ANULADA'
        ).count()

        # Ventas por cajero (hoy)
        ventas_por_cajero = Venta.objects.filter(
            fecha_venta__date=hoy,
            estado='COMPLETADA'
        ).values(
            'usuario__first_name', 'usuario__last_name', 'usuario__username'
        ).annotate(
            cantidad=Count('id'),
            total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField()),
        ).order_by('-total')

        # Categorias con mas ventas (mes)
        categorias_ventas = DetalleVenta.objects.filter(
            venta__fecha_venta__date__gte=inicio_mes,
            venta__estado='COMPLETADA'
        ).values(
            'producto__categoria__nombre'
        ).annotate(
            total=Sum('total_linea'),
            cantidad=Sum('cantidad'),
        ).order_by('-total')[:5]

        # Ultimas compras (inventario)
        ultimas_compras = Compra.objects.select_related(
            'registrado_por'
        ).order_by('-fecha_compra')[:5]

        context_admin = {
            'resumen_semana': resumen_semana,
            'resumen_mes': resumen_mes,
            'variacion_diaria': variacion_diaria,
            'total_ayer': total_ayer,
            'top_productos': top_productos,
            'productos_bajo_stock': productos_bajo_stock,
            'cantidad_bajo_stock': len(productos_bajo_stock),
            'inventario_total': inventario_total,
            'anulaciones_hoy': anulaciones_hoy,
            'ventas_por_cajero': ventas_por_cajero,
            'categorias_ventas': categorias_ventas,
            'ultimas_compras': ultimas_compras,
            'total_productos': Producto.objects.filter(activo=True).count(),
            'total_categorias': Categoria.objects.filter(activa=True).count(),
        }

    # ------------------------------------------------------------------
    # CONTEXTO FINAL
    # ------------------------------------------------------------------
    context = {
        'fecha_hoy': hoy,
        'hora_actual': ahora,
        'resumen_hoy': resumen_hoy,
        'efectivo_hoy': efectivo_hoy,
        'transferencia_hoy': transferencia_hoy,
        'tarjeta_hoy': tarjeta_hoy,
        'ultimas_ventas': ultimas_ventas,
        **context_admin,
    }

    if request.user.es_cajera:
        return render(request, 'reportes/dashboard_cajera.html', context)

    return render(request, 'reportes/dashboard.html', context)


# ============================================================================
# API - DATOS EN TIEMPO REAL (para Alpine.js polling)
# ============================================================================

@login_required
def api_metricas_hoy(request):
    """
    Endpoint JSON para actualizar metricas en tiempo real via Alpine.js
    """
    hoy = timezone.now().date()

    ventas_qs = Venta.objects.filter(
        fecha_venta__date=hoy,
        estado='COMPLETADA'
    )

    if request.user.es_cajera:
        ventas_qs = ventas_qs.filter(cajero=request.user)

    resumen = ventas_qs.aggregate(
        total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField()),
        cantidad=Count('id'),
    )

    pagos = Pago.objects.filter(
        venta__in=ventas_qs
    ).values('metodo').annotate(
        total=Coalesce(Sum('monto'), Decimal('0.00'), output_field=DecimalField()),
    )

    pagos_dict = {p['metodo']: float(p['total']) for p in pagos}

    return JsonResponse({
        'total_ventas': float(resumen['total']),
        'cantidad_ventas': resumen['cantidad'],
        'efectivo': pagos_dict.get('EFECTIVO', 0),
        'transferencia': pagos_dict.get('TRANSFERENCIA', 0),
        'tarjeta': pagos_dict.get('TARJETA', 0),
    })
