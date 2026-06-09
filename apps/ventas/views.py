"""
Views para el Punto de Venta (POS)
apps/ventas/views.py

Este módulo maneja:
1. Interfaz principal del POS
2. Búsqueda de productos con autocompletado
3. Información de stock en tiempo real
"""



from django.http import JsonResponse, HttpResponse

from apps.configuracion.models import AccesoRapidoPOS
from apps.configuracion.utils import get_config

from .models import Venta, FinanciacionCooperativa
from apps.configuracion.decorators import requiere_modulo

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.db import models, transaction
from django.db.models import Q, Sum
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from decimal import Decimal
import json
from utils.impresoras.manager import print_manager
from apps.auditoria.models import Auditoria, get_client_ip


from apps.ventas.models import Venta, DetalleVenta, Pago
from apps.productos.models import Producto
from apps.inventario.models import Lote, MovimientoLote
from apps.inventario.fifo_logic import procesar_venta_fifo

import pytz


from django.db import transaction
from apps.sync import events as sync_events

from apps.ventas.services import (
    procesar_venta_service,
    anular_venta_service,
    ErrorVentaBase,
)

# ============================================
# VISTA PRINCIPAL DEL POS
# ============================================

@login_required
def punto_venta(request):
    """
    Vista principal del Punto de Venta.
    Ahora hidrata métodos de pago desde ConfiguracionNegocio.
    """
    if not hasattr(request.user, 'rol'):
        messages.error(request, 'Usuario sin rol asignado.')
        return redirect('admin:index')
 
    from apps.configuracion.utils import get_config
    config = get_config()
 
    # Construir lista de métodos de pago habilitados
    metodos_pago = []
    if config.pago_efectivo:
        metodos_pago.append({
            'id': 'efectivo',
            'label': 'Efectivo',
            'icon': 'cash',
        })
    if config.pago_transferencia:
        metodos_pago.append({
            'id': 'transferencia',
            'label': 'Transferencia',
            'icon': 'transfer',
        })
    if config.pago_tarjeta:
        metodos_pago.append({
            'id': 'tarjeta',
            'label': 'Tarjeta',
            'icon': 'card',
        })

    from apps.cuentas_por_cobrar.models import MetodoPlazoCredito
    metodos_credito = [
        {
            'id': m.id,
            'nombre': m.nombre,
            'tipo': m.tipo,
            'dias_vencimiento': m.dias_vencimiento,
            'cantidad_cuotas': m.cantidad_cuotas,
            'frecuencia': m.frecuencia,
            'inicial_minima_porcentaje': str(m.inicial_minima_porcentaje),
        }
        for m in MetodoPlazoCredito.objects.filter(activo=True).order_by('nombre')
    ]
 
    context = {
        'usuario': request.user,
        'pos_config_json': {
            'metodos_pago': metodos_pago,
            'metodos_credito': metodos_credito,
            'permite_mixto': len(metodos_pago) > 1,
            'permitir_inventario_negativo': config.permitir_inventario_negativo,
            'modulo_ecf': config.modulo_ecf,
        },
    }
 
    return render(request, 'pos/punto_venta.html', context)


def _stock_disponible_producto(producto):
    return Lote.objects.filter(
        producto=producto,
        cantidad_actual__gt=0,
        activo=True
    ).aggregate(total=Sum('cantidad_actual'))['total'] or 0


def _producto_pos_data(producto):
    stock_disponible = _stock_disponible_producto(producto)
    return {
        'id': producto.id,
        'nombre': producto.nombre,
        'sku': producto.sku,
        'codigo_barras': producto.codigo_barras or '',
        'precio_venta': float(producto.precio_venta),
        'stock_disponible': stock_disponible,
        'tiene_stock': stock_disponible > 0,
        'categoria': producto.categoria.nombre if producto.categoria else 'Sin categoria',
        'categoria_id': producto.categoria_id,
        'precio_formateado': f"${producto.precio_venta:,.2f}",
        'imagen': producto.imagen.url if producto.imagen else '',
    }


def _acceso_rapido_pos_data(acceso):
    return {
        'id': acceso.id,
        'etiqueta': acceso.etiqueta_visible,
        'tipo': acceso.tipo,
        'color': acceso.color,
        'producto_id': acceso.producto_id,
        'categoria_id': acceso.categoria_id,
        'producto_nombre': acceso.producto.nombre if acceso.producto_id else '',
        'categoria_nombre': acceso.categoria.nombre if acceso.categoria_id else '',
    }
 
# ============================================
# API: BUSCAR PRODUCTOS
# ============================================

@login_required
@require_http_methods(["GET"])
def buscar_productos(request):
    """
    API para buscar productos en el POS.
    
    Búsqueda por:
    - Nombre del producto (coincidencia parcial)
    - SKU (exacto o parcial)
    - Código de barras (exacto)
    
    Query params:
        q: término de búsqueda (mínimo 2 caracteres)
        limit: cantidad máxima de resultados (default: 20)
    
    Returns:
        JSON con lista de productos encontrados incluyendo:
        - id, nombre, sku, codigo_barras
        - precio_venta
        - stock_disponible (suma de todos los lotes FIFO)
        - tiene_stock (boolean)
    """
    query = request.GET.get('q', '').strip()
    categoria_id = request.GET.get('categoria_id')
    try:
        limit = int(request.GET.get('limit', 20))
    except (TypeError, ValueError):
        limit = 20
    
    # Validar que el término tenga al menos 2 caracteres
    if len(query) < 2 and not categoria_id:
        return JsonResponse({
            'success': True,
            'productos': [],
            'message': 'Ingrese al menos 2 caracteres'
        })
    
    # Buscar productos activos que coincidan
    productos = Producto.objects.filter(activo=True).select_related('categoria')

    if categoria_id:
        productos = productos.filter(categoria_id=categoria_id, categoria__activa=True)

    if query:
        productos = productos.filter(
            # Buscar por nombre (case-insensitive)
            Q(nombre__icontains=query) |
            # O por SKU
            Q(sku__icontains=query) |
            # O por codigo de barras (exacto)
            Q(codigo_barras__iexact=query) |
            # O por categoria para accesos rapidos
            Q(categoria__nombre__icontains=query)
        )

    productos = productos[:limit]
    productos_data = [_producto_pos_data(producto) for producto in productos]
    
    return JsonResponse({
        'success': True,
        'productos': productos_data,
        'total_encontrados': len(productos_data)
    })


# ============================================
# API: OBTENER PRODUCTO POR CÓDIGO DE BARRAS
# ============================================

@login_required
@require_http_methods(["GET"])
def producto_por_codigo(request, codigo_barras):
    """
    API para obtener un producto específico por código de barras.
    
    Se usa cuando el scanner lee un código.
    Si el producto existe, retorna su información completa.
    
    Path params:
        codigo_barras: código de barras del producto
    
    Returns:
        JSON con datos del producto o error si no existe
    """
    try:
        producto = Producto.objects.select_related('categoria').get(
            codigo_barras=codigo_barras,
            activo=True
        )
        
        return JsonResponse({
            'success': True,
            'producto': _producto_pos_data(producto),
        })
    
    except Producto.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': f'Producto con código de barras "{codigo_barras}" no encontrado'
        }, status=404)


@login_required
@require_http_methods(["GET"])
def producto_por_id(request, producto_id):
    """
    API para consultar un producto activo por ID desde el POS.

    Se usa para accesos rapidos de producto, manteniendo precio y stock frescos.
    """
    try:
        producto = Producto.objects.select_related('categoria').get(
            id=producto_id,
            activo=True,
        )
    except Producto.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Producto no encontrado o inactivo',
        }, status=404)

    return JsonResponse({
        'success': True,
        'producto': _producto_pos_data(producto),
    })


@login_required
@require_http_methods(["GET"])
def accesos_rapidos_pos(request):
    """Lista los botones configurables globales del POS."""
    accesos = (
        AccesoRapidoPOS.objects
        .filter(activo=True)
        .select_related('producto', 'categoria')
        .order_by('orden', 'id')
    )

    accesos_validos = []
    for acceso in accesos:
        if acceso.tipo == AccesoRapidoPOS.TIPO_PRODUCTO:
            if acceso.producto_id and acceso.producto.activo:
                accesos_validos.append(_acceso_rapido_pos_data(acceso))
        elif acceso.tipo == AccesoRapidoPOS.TIPO_CATEGORIA:
            if acceso.categoria_id and acceso.categoria.activa:
                accesos_validos.append(_acceso_rapido_pos_data(acceso))

    return JsonResponse({
        'success': True,
        'accesos': accesos_validos,
    })


# ============================================
# API: VERIFICAR STOCK DISPONIBLE
# ============================================

@login_required
@require_http_methods(["GET"])
def verificar_stock(request, producto_id):
    """
    API para verificar stock disponible de un producto.
    
    Útil para validar antes de agregar al carrito.
    
    Path params:
        producto_id: ID del producto
    
    Query params:
        cantidad: cantidad que se desea verificar (opcional)
    
    Returns:
        JSON con información de stock:
        - stock_disponible: cantidad total disponible
        - stock_suficiente: boolean si hay suficiente para la cantidad solicitada
        - lotes_disponibles: lista de lotes con stock (ordenados por FIFO)
    """
    cantidad_solicitada = int(request.GET.get('cantidad', 1))
    
    try:
        producto = Producto.objects.get(id=producto_id, activo=True)
        
        # Obtener lotes con stock ordenados por FIFO
        lotes = Lote.objects.filter(
            producto=producto,
            cantidad_actual__gt=0,
            activo=True
        ).order_by('fecha_compra', 'id')
        
        stock_total = sum(lote.cantidad_actual for lote in lotes)
        stock_suficiente = stock_total >= cantidad_solicitada
        
        # Información de lotes (útil para debugging/admin)
        lotes_info = [
            {
                'numero_lote': lote.numero_lote,
                'cantidad_disponible': lote.cantidad_actual,
                'fecha_compra': lote.fecha_compra.strftime('%d/%m/%Y'),
            }
            for lote in lotes
        ]
        
        return JsonResponse({
            'success': True,
            'producto_id': producto.id,
            'producto_nombre': producto.nombre,
            'stock_disponible': stock_total,
            'cantidad_solicitada': cantidad_solicitada,
            'stock_suficiente': stock_suficiente,
            'lotes_disponibles': lotes_info,
            'alerta': None if stock_suficiente else f'Stock insuficiente. Disponible: {stock_total}'
        })
    
    except Producto.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Producto no encontrado'
        }, status=404)
    

@login_required
@require_http_methods(["POST"])
def procesar_venta(request):
    """
    Procesa una venta desde el POS.

    Este view es delgado: parsea el JSON del request, delega a
    procesar_venta_service la lógica de negocio (validaciones,
    transacciones, hooks de sync/print/e-CF), y traduce el resultado
    o las excepciones tipadas a JsonResponse.

    POST Body (JSON):
    {
        "carrito": [
            {"id": 1, "cantidad": 2, "precio_venta": 30.00,
             "descuento": 5.00},
            ...
        ],
        "metodo_pago": "efectivo" | "transferencia" | "tarjeta" | "mixto",
        "monto_efectivo": 100.00,         // requerido si mixto
        "monto_transferencia": 0.00,      // opcional
        "monto_tarjeta": 0.00,            // opcional
        "referencia_tarjeta": "...",      // opcional
        "total": 95.00,
        "cliente_id": 5,                  // opcional, null = CONTADO
        "tipo_ecf": "32"                  // opcional, "31" o "32" (default "32")
    }

    Returns:
        200: {"success": true, "venta": {...}, "mensaje": "..."}
        400: {"success": false, "error": "..."}    # validación de negocio
        404: {"success": false, "error": "..."}    # producto inexistente
        500: {"success": false, "error": "..."}    # excepción no manejada
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse(
            {'success': False, 'error': 'JSON inválido en el request.'},
            status=400,
        )

    try:
        venta = procesar_venta_service(
            usuario=request.user,
            datos=data,
            ip_address=get_client_ip(request),
        )
    except ErrorVentaBase as exc:
        # Errores de negocio esperados — el service ya armó el mensaje
        # legible. El status code lo trae la excepción.
        return JsonResponse(
            {'success': False, 'error': str(exc)},
            status=exc.status_code,
        )
    except Exception as exc:
        # Cualquier excepción no anticipada. Log completo, mensaje
        # genérico al cliente.
        import traceback
        print(f'❌ ERROR no manejado en procesar_venta: {exc}')
        traceback.print_exc()
        return JsonResponse(
            {'success': False, 'error': f'Error al procesar la venta: {exc}'},
            status=500,
        )

    return JsonResponse({
        'success': True,
        'venta': {
            'id': venta.id,
            'numero_venta': venta.numero_venta,
            'total': float(venta.total),
            'condicion_pago': venta.condicion_pago,
            'fecha': venta.fecha_venta.strftime('%d/%m/%Y %H:%M'),
            'items_count': venta.detalles.count(),
            'saldo_credito': float(getattr(getattr(venta, 'cuenta_por_cobrar', None), 'saldo', 0) or 0),
        },
        'mensaje': f'Venta {venta.numero_venta} procesada exitosamente',
    })

# ============================================
# VISTA DE CONFIRMACIÓN DE VENTA
# ============================================

@login_required
def venta_exitosa(request, venta_id):
    """
    Muestra la confirmación de una venta exitosa.
    Incluye el detalle para reimprimir o ver el resumen.
    """
    venta = get_object_or_404(Venta, id=venta_id)
    
    # Obtener detalles y pagos
    detalles = venta.detalles.all().select_related('producto')
    pagos = venta.pagos.all()
    
    context = {
        'venta': venta,
        'detalles': detalles,
        'pagos': pagos,
        'config': get_config(),
    }
    
    return render(request, 'pos/venta_exitosa.html', context)




"""
Views para Financiacion Cooperativa
apps/ventas/financiacion_views.py

Flujo:
1. Se procesa venta normal en el POS (inventario afectado)
2. Se registran datos del cliente cooperativa
3. Se genera PDF formal tipo factura
4. Se abre dialogo de impresion del navegador
"""





@login_required
@require_http_methods(["POST"])
@requiere_modulo('financiacion_coop')
def registrar_financiacion(request):
    """
    Registra datos de financiacion cooperativa para una venta.

    Datos esperados (JSON):
    {
        "venta_id": 1,
        "nombre_cliente": "Juan Perez",
        "cedula_cliente": "001-0000000-0",
        "telefono_cliente": "809-000-0000",
        "direccion_cliente": "Calle X, Santo Domingo",
        "nombre_cooperativa": "Cooperativa Nacional",
        "codigo_aprobacion": "COOP-2026-001",
        "notas": "..."
    }
    """
    try:
        data = json.loads(request.body)

        venta_id = data.get('venta_id')
        if not venta_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de venta requerido'
            }, status=400)

        venta = get_object_or_404(Venta, id=venta_id)

        if hasattr(venta, 'financiacion'):
            return JsonResponse({
                'success': False,
                'error': 'Esta venta ya tiene datos de financiacion'
            })

        with transaction.atomic():
            financiacion = FinanciacionCooperativa.objects.create(
                venta=venta,
                nombre_cliente=data['nombre_cliente'].strip(),
                cedula_cliente=data['cedula_cliente'].strip(),
                telefono_cliente=data.get('telefono_cliente', '').strip() or None,
                direccion_cliente=data.get('direccion_cliente', '').strip() or None,
                nombre_cooperativa=data.get('nombre_cooperativa', '').strip(),
                codigo_aprobacion=data.get('codigo_aprobacion', '').strip() or None,
                notas=data.get('notas', '').strip() or None,
                usuario=request.user,
            )

        return JsonResponse({
            'success': True,
            'message': 'Datos de financiacion registrados',
            'financiacion_id': financiacion.id,
            'venta_numero': venta.numero_venta,
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@requiere_modulo('financiacion_coop')
def generar_pdf_financiacion(request, venta_id):
    """
    Genera PDF formal tipo factura para venta con financiacion.
    Usa ReportLab para generar el PDF con logo en color.
    """
    venta = get_object_or_404(Venta, id=venta_id)

    if not hasattr(venta, 'financiacion'):
        return JsonResponse({
            'success': False,
            'error': 'Esta venta no tiene datos de financiacion'
        }, status=400)

    financiacion = venta.financiacion
    detalles = venta.detalles.select_related('producto').all()
    pagos = venta.pagos.all()

    from .pdf_financiacion import generar_factura_cooperativa
    pdf_buffer = generar_factura_cooperativa(venta, financiacion, detalles, pagos)

    financiacion.pdf_generado = True
    financiacion.save()

    response = HttpResponse(pdf_buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = (
        f'inline; filename="factura_{venta.numero_venta}.pdf"'
    )
    return response


@login_required
@requiere_modulo('financiacion_coop')
def vista_financiacion(request, venta_id):
    """
    Vista HTML que muestra datos de financiacion
    y permite generar/imprimir el PDF.
    """
    venta = get_object_or_404(Venta, id=venta_id)

    financiacion = getattr(venta, 'financiacion', None)
    detalles = venta.detalles.select_related('producto').all()

    context = {
        'venta': venta,
        'financiacion': financiacion,
        'detalles': detalles,
    }

    return render(request, 'pos/financiacion_detalle.html', context)


@login_required
@requiere_modulo('financiacion_coop')
def lista_financiaciones(request):
    """Lista de ventas con financiacion cooperativa"""

    financiaciones = FinanciacionCooperativa.objects.select_related(
        'venta', 'usuario'
    ).all()

    context = {
        'financiaciones': financiaciones,
    }

    return render(request, 'pos/financiacion_lista.html', context)





@login_required
def vista_anulaciones(request):
    """
    Página para gestionar anulaciones de venta.
    Solo accesible por ADMIN y SYSADMIN.
    """
    if not request.user.tiene_permiso('ventas.anular'):
        messages.error(request, 'No tienes permisos para acceder a esta sección.')
        return redirect('pos:punto_venta')
 
    config = get_config()
 
    # Obtener ventas recientes (últimos 30 días, máx 100)
    from datetime import timedelta
    fecha_desde = timezone.now() - timedelta(days=30)
 
    ventas_qs = Venta.objects.filter(
        fecha_venta__gte=fecha_desde
    ).select_related('usuario', 'anulada_por', 'cliente').order_by('-fecha_venta')[:100]
 
    ventas_data = []
    for v in ventas_qs:
        ventas_data.append({
            'id': v.id,
            'numero_venta': v.numero_venta,
            'fecha': v.fecha_venta.strftime('%d/%m/%Y %H:%M'),
            'fecha_iso': v.fecha_venta.strftime('%Y-%m-%d'),
            'cajero': v.usuario.get_full_name() or v.usuario.username,
            'cliente': str(v.cliente) if v.cliente else 'Contado',
            'total': str(v.total),
            'estado': v.estado,
            'puede_anularse': v.puede_anularse(),
            'motivo_anulacion': v.motivo_anulacion or '',
            'anulada_por': (v.anulada_por.get_full_name() or v.anulada_por.username) if v.anulada_por else '',
            'fecha_anulacion': v.fecha_anulacion.strftime('%d/%m/%Y %H:%M') if v.fecha_anulacion else '',
        })
 
    context = {
        'init_data_json': {
            'ventas': ventas_data,
            'dias_limite': config.dias_anulacion,
        },
    }
 
    return render(request, 'pos/anulaciones.html', context)
 
 
# ============================================
# API: ANULAR VENTA
# ============================================
 
@login_required
@require_http_methods(["POST"])
def api_anular_venta(request):
    """
    Anula una venta existente.

    Delega toda la lógica de negocio (validación de permisos, plazo,
    devolución FIFO, auditoría, hook de NC tipo 34) a
    anular_venta_service.

    POST Body (JSON):
    {
        "venta_id": 123,
        "motivo": "Texto obligatorio del motivo (mín. 10 caracteres)"
    }

    Returns:
        200: {"success": true, "message": "...", "venta": {...}}
        400: errores de validación de negocio (motivo, plazo, etc.)
        403: usuario sin permisos (rol != ADMIN/SYSADMIN)
        404: venta inexistente
        500: error inesperado o falla FIFO
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse(
            {'success': False, 'error': 'JSON inválido en el request.'},
            status=400,
        )

    venta_id = data.get('venta_id')
    motivo = data.get('motivo', '')

    if not venta_id:
        return JsonResponse(
            {'success': False, 'error': 'venta_id es requerido.'},
            status=400,
        )

    try:
        venta = anular_venta_service(
            usuario=request.user,
            venta_id=venta_id,
            motivo=motivo,
            ip_address=get_client_ip(request),
        )
    except ErrorVentaBase as exc:
        return JsonResponse(
            {'success': False, 'error': str(exc)},
            status=exc.status_code,
        )
    except Exception as exc:
        import traceback
        print(f'❌ ERROR no manejado en api_anular_venta: {exc}')
        traceback.print_exc()
        return JsonResponse(
            {'success': False, 'error': f'Error inesperado: {exc}'},
            status=500,
        )

    return JsonResponse({
        'success': True,
        'message': f'Venta {venta.numero_venta} anulada exitosamente.',
        'venta': {
            'id': venta.id,
            'numero_venta': venta.numero_venta,
            'total': str(venta.total),
            'estado': 'ANULADA',
            'motivo_anulacion': venta.motivo_anulacion,
            'anulada_por': (
                request.user.get_full_name() or request.user.username
            ),
            'fecha_anulacion': venta.fecha_anulacion.strftime('%d/%m/%Y %H:%M'),
        },
    })
