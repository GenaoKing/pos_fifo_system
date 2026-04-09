from django.shortcuts import render

# Create your views here.
"""
apps/reportes/views.py
Dashboard y vistas de reportes
"""

import json
from datetime import date, timedelta
from decimal import Decimal

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, FileResponse
from django.db.models import Sum, Count, F, Q, Avg, DecimalField
from django.db.models.functions import Coalesce
from django.utils import timezone
from decimal import Decimal
from datetime import timedelta
from django.db.models.functions import Coalesce, TruncDate

from .models import CierreCaja, TopProducto, InventarioValorizado
from apps.ventas.models import Venta, DetalleVenta, Pago
from apps.productos.models import Producto, Categoria
from apps.inventario.models import Lote, Compra
from apps.usuarios.models import Usuario


from .pdf_generator import PDFGenerator
from .report_manager import ReporteManager

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




def es_admin(user):
    return user.is_authenticated and user.rol in ['ADMIN', 'SYSADMIN']


# ============================================================================
# PAGINA PRINCIPAL REPORTES ON-DEMAND
# ============================================================================

@login_required
def reportes_on_demand(request):
    """
    Pagina principal de reportes on-demand.
    Solo ADMIN puede acceder.
    """
    if not es_admin(request.user):
        from django.shortcuts import redirect
        return redirect('reportes:dashboard')

    # Lista de cajeros para el select
    cajeros = Usuario.objects.filter(
        activo=True,
    ).values('id', 'username', 'first_name', 'last_name', 'rol')

    context = {
        'cajeros': list(cajeros),
        'fecha_hoy': timezone.now().date().isoformat(),
    }
    return render(request, 'reportes/on_demand.html', context)


# ============================================================================
# API: GENERAR CIERRE DE CAJA MANUAL
# ============================================================================

@login_required
def api_cierre_manual(request):
    """
    POST: Genera cierre de caja para una fecha especifica
    """
    if not es_admin(request.user):
        return JsonResponse({'error': 'Sin permisos'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        fecha_str = data.get('fecha')

        if not fecha_str:
            return JsonResponse({'error': 'Fecha requerida'}, status=400)

        fecha = date.fromisoformat(fecha_str)

        # No permitir fechas futuras
        if fecha > timezone.now().date():
            return JsonResponse({'error': 'No se puede generar cierre para fechas futuras'}, status=400)

        cierre = ReporteManager.generar_cierre_diario(
            fecha=fecha,
            generado_automaticamente=False,
            usuario=request.user
        )

        # Generar PDF
        pdf_path = None
        try:
            pdf_path = PDFGenerator.generar_cierre_caja(cierre.id)
            if pdf_path and not cierre.archivo_pdf:
                cierre.archivo_pdf = pdf_path
                cierre.save()
        except Exception:
            pass  # PDF es opcional, no bloquear

        # Construir respuesta
        response_data = {
            'success': True,
            'cierre': {
                'id': cierre.id,
                'fecha': cierre.fecha.isoformat(),
                'cantidad_ventas': cierre.cantidad_ventas,
                'total_ventas': str(cierre.total_ventas or Decimal('0.00')),
                'total_efectivo': str(cierre.total_efectivo or Decimal('0.00')),
                'total_transferencia': str(cierre.total_transferencia or Decimal('0.00')),
                'resumen_cajeros': cierre.resumen_cajeros or {},
                'generado_automaticamente': cierre.generado_automaticamente,
                'tiene_pdf': bool(cierre.archivo_pdf),
            }
        }

        return JsonResponse(response_data)

    except ValueError as e:
        return JsonResponse({'error': f'Fecha invalida: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ============================================================================
# API: VENTAS POR PERIODO
# ============================================================================

@login_required
def api_ventas_periodo(request):
    """
    POST: Consulta ventas filtradas por periodo y cajero opcional
    """
    if not es_admin(request.user):
        return JsonResponse({'error': 'Sin permisos'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        fecha_inicio = date.fromisoformat(data.get('fecha_inicio', ''))
        fecha_fin = date.fromisoformat(data.get('fecha_fin', ''))
        cajero_id = data.get('cajero_id')

        if fecha_inicio > fecha_fin:
            return JsonResponse({'error': 'Fecha inicio debe ser menor a fecha fin'}, status=400)

        # Query base
        ventas_qs = Venta.objects.filter(
            fecha_venta__date__gte=fecha_inicio,
            fecha_venta__date__lte=fecha_fin,
            estado='COMPLETADA'
        )

        if cajero_id:
            ventas_qs = ventas_qs.filter(cajero_id=cajero_id)

        # Totales generales
        totales = ventas_qs.aggregate(
            cantidad=Count('id'),
            total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField()),
            descuentos=Coalesce(Sum('descuento_total'), Decimal('0.00'), output_field=DecimalField()),
        )

        # Totales por metodo de pago
        pagos = Pago.objects.filter(
            venta__in=ventas_qs
        ).values('metodo').annotate(
            total=Coalesce(Sum('monto'), Decimal('0.00'), output_field=DecimalField()),
            cantidad=Count('id'),
        )

        pagos_resumen = {p['metodo']: {'total': str(p['total']), 'cantidad': p['cantidad']} for p in pagos}

        # Ventas por dia (para grafico)
        ventas_por_dia = ventas_qs.annotate(
            dia=TruncDate('fecha_venta')
        ).values('dia').annotate(
            total=Sum('total'),
            cantidad=Count('id'),
        ).order_by('dia')

        # Ultimas ventas del periodo
        ultimas = ventas_qs.select_related('usuario').order_by('-fecha_venta')[:20]
        ventas_lista = [{
            'numero': v.numero_venta,
            'fecha': v.fecha_venta.strftime('%d/%m/%Y %H:%M'),
            'cajero': v.usuario.get_short_name() if v.usuario else 'N/A',
            'total': str(v.total),
            'descuento': str(v.descuento_total or Decimal('0.00')),
        } for v in ultimas]

        return JsonResponse({
            'success': True,
            'periodo': {
                'fecha_inicio': fecha_inicio.isoformat(),
                'fecha_fin': fecha_fin.isoformat(),
            },
            'totales': {
                'cantidad': totales['cantidad'],
                'total': str(totales['total']),
                'descuentos': str(totales['descuentos']),
            },
            'pagos': pagos_resumen,
            'ventas_por_dia': [{
                'dia': v['dia'].isoformat(),
                'total': str(v['total']),
                'cantidad': v['cantidad'],
            } for v in ventas_por_dia],
            'ventas': ventas_lista,
        })

    except ValueError as e:
        return JsonResponse({'error': f'Datos invalidos: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ============================================================================
# API: TOP PRODUCTOS
# ============================================================================

@login_required
def api_top_productos(request):
    """
    POST: Genera ranking de productos mas vendidos
    """
    if not es_admin(request.user):
        return JsonResponse({'error': 'Sin permisos'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        fecha_inicio = date.fromisoformat(data.get('fecha_inicio', ''))
        fecha_fin = date.fromisoformat(data.get('fecha_fin', ''))
        limite = int(data.get('limite', 10))

        if fecha_inicio > fecha_fin:
            return JsonResponse({'error': 'Fecha inicio debe ser menor a fecha fin'}, status=400)

        if limite not in [5, 10, 20]:
            limite = 10

        # Query directa para ranking
        ranking = DetalleVenta.objects.filter(
            venta__fecha_venta__date__gte=fecha_inicio,
            venta__fecha_venta__date__lte=fecha_fin,
            venta__estado='COMPLETADA'
        ).values(
            'producto__id',
            'producto__nombre',
            'producto__sku',
        ).annotate(
            cantidad_vendida=Sum('cantidad'),
            total_vendido=Sum('total_linea'),
            transacciones=Count('venta', distinct=True),
        ).order_by('-cantidad_vendida')[:limite]

        productos = [{
            'posicion': idx + 1,
            'nombre': p['producto__nombre'],
            'sku': p['producto__sku'],
            'cantidad': str(p['cantidad_vendida']),
            'total': str(p['total_vendido']),
            'transacciones': p['transacciones'],
        } for idx, p in enumerate(ranking)]

        # Guardar en modelo si se desea
        try:
            ReporteManager.generar_top_productos(
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
                limite=limite
            )
        except Exception:
            pass  # No critico

        return JsonResponse({
            'success': True,
            'periodo': {
                'fecha_inicio': fecha_inicio.isoformat(),
                'fecha_fin': fecha_fin.isoformat(),
                'limite': limite,
            },
            'productos': productos,
        })

    except ValueError as e:
        return JsonResponse({'error': f'Datos invalidos: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ============================================================================
# API: INVENTARIO VALORIZADO
# ============================================================================

@login_required
def api_inventario_valorizado(request):
    """
    POST: Genera snapshot del inventario valorizado
    """
    if not es_admin(request.user):
        return JsonResponse({'error': 'Sin permisos'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        fecha_str = data.get('fecha')
        fecha = date.fromisoformat(fecha_str) if fecha_str else timezone.now().date()

        # Consultar lotes activos con stock
        lotes = Lote.objects.filter(
            cantidad_actual__gt=0,
            activo=True,
        ).select_related('producto').order_by(
            'producto__nombre', 'fecha_compra'
        )

        # Agrupar por producto
        productos_dict = {}
        for lote in lotes:
            pid = lote.producto_id
            if pid not in productos_dict:
                productos_dict[pid] = {
                    'nombre': lote.producto.nombre,
                    'sku': lote.producto.sku,
                    'cantidad_total': Decimal('0'),
                    'valor_total': Decimal('0'),
                    'lotes': [],
                }

            valor_lote = lote.cantidad_actual * lote.costo_unitario
            productos_dict[pid]['cantidad_total'] += lote.cantidad_actual
            productos_dict[pid]['valor_total'] += valor_lote
            productos_dict[pid]['lotes'].append({
                'numero': lote.numero_lote,
                'fecha_compra': lote.fecha_compra.strftime('%d/%m/%Y') if lote.fecha_compra else 'N/A',
                'cantidad': str(lote.cantidad_actual),
                'costo_unitario': str(lote.costo_unitario),
                'valor': str(valor_lote),
            })

        # Convertir a lista
        productos_lista = []
        total_unidades = Decimal('0')
        total_valor = Decimal('0')

        for pid, p in productos_dict.items():
            costo_promedio = (p['valor_total'] / p['cantidad_total']) if p['cantidad_total'] > 0 else Decimal('0')
            productos_lista.append({
                'nombre': p['nombre'],
                'sku': p['sku'],
                'cantidad_total': str(p['cantidad_total']),
                'costo_promedio': str(costo_promedio.quantize(Decimal('0.01'))),
                'valor_total': str(p['valor_total']),
                'lotes': p['lotes'],
            })
            total_unidades += p['cantidad_total']
            total_valor += p['valor_total']

        # Guardar snapshot via ReporteManager
        try:
            ReporteManager.generar_inventario_valorizado(fecha=fecha)
        except Exception:
            pass

        return JsonResponse({
            'success': True,
            'fecha': fecha.isoformat(),
            'resumen': {
                'total_productos': len(productos_lista),
                'total_unidades': str(total_unidades),
                'valor_total': str(total_valor),
            },
            'productos': productos_lista,
        })

    except ValueError as e:
        return JsonResponse({'error': f'Datos invalidos: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ============================================================================
# API: VENTAS POR CAJERO
# ============================================================================

@login_required
def api_ventas_cajero(request):
    """
    POST: Comparativa de ventas entre cajeros
    """
    if not es_admin(request.user):
        return JsonResponse({'error': 'Sin permisos'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        fecha_inicio = date.fromisoformat(data.get('fecha_inicio', ''))
        fecha_fin = date.fromisoformat(data.get('fecha_fin', ''))

        if fecha_inicio > fecha_fin:
            return JsonResponse({'error': 'Fecha inicio debe ser menor a fecha fin'}, status=400)

        ventas_qs = Venta.objects.filter(
            fecha_venta__date__gte=fecha_inicio,
            fecha_venta__date__lte=fecha_fin,
            estado='COMPLETADA'
        )

        # Agrupar por cajero
        por_cajero = ventas_qs.values(
            'usuario__id',
            'usuario__username',
            'usuario__first_name',
            'usuario__last_name',
        ).annotate(
            cantidad=Count('id'),
            suma_total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField()),
            descuentos=Coalesce(Sum('descuento_total'), Decimal('0.00'), output_field=DecimalField()),
        ).order_by('-suma_total')

        cajeros_lista = []
        total_general = Decimal('0')

        for c in por_cajero:
            nombre = c['usuario__first_name'] or c['usuario__username']
            if c['usuario__last_name']:
                nombre += f" {c['usuario__last_name']}"

            promedio = (c['suma_total'] / c['cantidad']) if c['cantidad'] > 0 else Decimal('0.00')

            cajeros_lista.append({
                'nombre': nombre.strip(),
                'cantidad': c['cantidad'],
                'total': str(c['suma_total']),
                'promedio': str(promedio.quantize(Decimal('0.01'))),
                'descuentos': str(c['descuentos']),
            })
            total_general += c['suma_total']

        # Calcular porcentajes
        for c in cajeros_lista:
            if total_general > 0:
                pct = (Decimal(c['total']) / total_general * 100).quantize(Decimal('0.1'))
                c['porcentaje'] = str(pct)
            else:
                c['porcentaje'] = '0.0'

        # Desglose por metodo de pago por cajero
        pagos_por_cajero = Pago.objects.filter(
            venta__in=ventas_qs
        ).values(
            'venta__usuario__username',
            'metodo'
        ).annotate(
            total=Sum('monto')
        )

        pagos_desglose = {}
        for p in pagos_por_cajero:
            user = p['venta__usuario__username']
            if user not in pagos_desglose:
                pagos_desglose[user] = {}
            pagos_desglose[user][p['metodo']] = str(p['total'])

        return JsonResponse({
            'success': True,
            'periodo': {
                'fecha_inicio': fecha_inicio.isoformat(),
                'fecha_fin': fecha_fin.isoformat(),
            },
            'total_general': str(total_general),
            'cajeros': cajeros_lista,
            'pagos_desglose': pagos_desglose,
        })

    except ValueError as e:
        return JsonResponse({'error': f'Datos invalidos: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ============================================================================
# DESCARGA PDF GENERICO
# ============================================================================

@login_required
def descargar_pdf_cierre(request, cierre_id):
    """
    Descarga PDF de un cierre de caja
    """
    if not es_admin(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    from django.shortcuts import get_object_or_404
    cierre = get_object_or_404(CierreCaja, id=cierre_id)

    # Generar PDF si no existe
    if not cierre.archivo_pdf:
        try:
            pdf_path = PDFGenerator.generar_cierre_caja(cierre.id)
            cierre.archivo_pdf = pdf_path
            cierre.save()
        except Exception as e:
            return JsonResponse({'error': f'Error generando PDF: {str(e)}'}, status=500)

    import os
    from django.conf import settings

    # Determinar ruta absoluta
    if os.path.isabs(str(cierre.archivo_pdf)):
        filepath = str(cierre.archivo_pdf)
    else:
        filepath = os.path.join(settings.MEDIA_ROOT, str(cierre.archivo_pdf))

    if not os.path.exists(filepath):
        return JsonResponse({'error': 'Archivo PDF no encontrado'}, status=404)

    return FileResponse(
        open(filepath, 'rb'),
        content_type='application/pdf',
        as_attachment=True,
        filename=f"cierre_caja_{cierre.fecha}.pdf"
    )