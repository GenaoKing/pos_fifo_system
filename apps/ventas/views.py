"""
Views para el Punto de Venta (POS)
apps/ventas/views.py

Este módulo maneja:
1. Interfaz principal del POS
2. Búsqueda de productos con autocompletado
3. Información de stock en tiempo real
"""



from django.http import JsonResponse, HttpResponse
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

from apps.productos.models import Producto
import pytz


# ============================================
# VISTA PRINCIPAL DEL POS
# ============================================

@login_required
def punto_venta(request):
    """
    Vista principal del Punto de Venta.
    
    Accesible por Admin y Cajera.
    Muestra la interfaz completa del POS con:
    - Panel de búsqueda de productos
    - Carrito de compras
    - Panel de pago
    """
    # Verificar que el usuario tenga permisos
    # (Admin y Cajera pueden acceder)
    if not hasattr(request.user, 'rol'):
        messages.error(request, 'Usuario sin rol asignado.')
        return redirect('admin:index')
    
    context = {
        'usuario': request.user,
    }
    
    return render(request, 'pos/punto_venta.html', context)


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
    limit = int(request.GET.get('limit', 20))
    
    # Validar que el término tenga al menos 2 caracteres
    if len(query) < 2:
        return JsonResponse({
            'success': True,
            'productos': [],
            'message': 'Ingrese al menos 2 caracteres'
        })
    
    # Buscar productos activos que coincidan
    productos = Producto.objects.filter(
        activo=True
    ).filter(
        # Buscar por nombre (case-insensitive)
        Q(nombre__icontains=query) |
        # O por SKU
        Q(sku__icontains=query) |
        # O por código de barras (exacto)
        Q(codigo_barras__iexact=query)
    ).select_related('categoria')[:limit]
    
    # Construir respuesta con información completa
    productos_data = []
    for producto in productos:
        # Calcular stock disponible (suma de todos los lotes activos)
        stock_disponible = Lote.objects.filter(
            producto=producto,
            cantidad_actual__gt=0,
            activo=True
        ).aggregate(
            total=Sum('cantidad_actual')
        )['total'] or 0
        
        productos_data.append({
            'id': producto.id,
            'nombre': producto.nombre,
            'sku': producto.sku,
            'codigo_barras': producto.codigo_barras or '',
            'precio_venta': float(producto.precio_venta),
            'stock_disponible': stock_disponible,
            'tiene_stock': stock_disponible > 0,
            'categoria': producto.categoria.nombre if producto.categoria else 'Sin categoría',
            # Información adicional útil para el POS
            'precio_formateado': f"${producto.precio_venta:,.2f}",
            'imagen': producto.imagen.url if producto.imagen else '',
        })
    
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
        
        # Calcular stock disponible
        stock_disponible = Lote.objects.filter(
            producto=producto,
            cantidad_actual__gt=0,
            activo=True
        ).aggregate(
            total=Sum('cantidad_actual')
        )['total'] or 0
        
        return JsonResponse({
            'success': True,
            'producto': {
                'id': producto.id,
                'nombre': producto.nombre,
                'sku': producto.sku,
                'codigo_barras': producto.codigo_barras,
                'precio_venta': float(producto.precio_venta),
                'stock_disponible': stock_disponible,
                'tiene_stock': stock_disponible > 0,
                'categoria': producto.categoria.nombre if producto.categoria else 'Sin categoría',
                'precio_formateado': f"${producto.precio_venta:,.2f}",
                'imagen': producto.imagen.url if producto.imagen else '',
            }
        })
    
    except Producto.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': f'Producto con código de barras "{codigo_barras}" no encontrado'
        }, status=404)


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
    Procesa una venta completa desde el POS.
    
    FLUJO:
    1. Recibe datos del carrito y pago
    2. Valida stock disponible
    3. Crea la venta (header)
    4. Crea los detalles de venta (líneas)
    5. Consume stock FIFO automáticamente
    6. Registra los pagos
    7. Retorna confirmación con número de venta
    
    POST Body (JSON):
    {
        "carrito": [
            {
                "id": 1,
                "cantidad": 2,
                "precio_venta": 30.00,
                "descuento": 5.00
            },
            ...
        ],
        "metodo_pago": "efectivo",  // 'efectivo', 'transferencia', 'mixto'
        "monto_efectivo": 100.00,
        "monto_transferencia": 0.00,
        "total": 95.00
    }
    
    Returns:
        JSON con éxito y número de venta, o error
    """
    try:
        # Parsear datos del request
        data = json.loads(request.body)
        
        carrito = data.get('carrito', [])
        metodo_pago = data.get('metodo_pago', 'efectivo')
        monto_efectivo = Decimal(str(data.get('monto_efectivo', 0)))
        monto_transferencia = Decimal(str(data.get('monto_transferencia', 0)))
        total_esperado = Decimal(str(data.get('total', 0)))
        
        # Validaciones básicas
        if not carrito:
            return JsonResponse({
                'success': False,
                'error': 'El carrito está vacío'
            }, status=400)
        
        # Usar transacción atómica
        with transaction.atomic():
            # ============================================
            # PASO 1: VALIDAR STOCK DISPONIBLE
            # ============================================
            for item in carrito:
                producto = get_object_or_404(Producto, id=item['id'])
                
                # Calcular stock disponible
                stock_disponible = Lote.objects.filter(
                    producto=producto,
                    cantidad_actual__gt=0,
                    activo=True
                ).aggregate(
                    total=models.Sum('cantidad_actual')
                )['total'] or 0
                
                cantidad_solicitada = item['cantidad']
                
                # Permitimos venta con stock negativo pero lo alertamos
                if cantidad_solicitada > stock_disponible:
                    print(f"⚠️ ALERTA: Venta con stock insuficiente - {producto.nombre}")
                    print(f"   Solicitado: {cantidad_solicitada}, Disponible: {stock_disponible}")
                    # Continuamos igual (permitido según tus especificaciones)
            
            # ============================================
            # PASO 2: CREAR VENTA (HEADER)
            # ============================================
            
            # Generar número de venta: VENTA-20260206-00001
            from django.utils import timezone as django_timezone
            santo_domingo_tz = pytz.timezone('America/Santo_Domingo')
            fecha_hoy = django_timezone.now().astimezone(santo_domingo_tz)
            fecha_str = fecha_hoy.strftime('%Y%m%d')
            #fecha_hoy = timezone.now().strftime('%Y%m%d')
            ultimo = Venta.objects.filter(
                numero_venta__startswith=f'VENTA-{fecha_str}'
            ).count()
            numero_venta = f'VENTA-{fecha_str}-{str(ultimo + 1).zfill(5)}'
            
            # Calcular totales
            subtotal = sum(
                Decimal(str(item['cantidad'])) * Decimal(str(item['precio_venta']))
                for item in carrito
            )
            descuento_total = sum(
                Decimal(str(item.get('descuento', 0)))
                for item in carrito
            )
            total = subtotal - descuento_total
            
            # Validar que el total coincida
            if abs(total - total_esperado) > Decimal('0.01'):
                return JsonResponse({
                    'success': False,
                    'error': f'Total no coincide. Esperado: ${total_esperado}, Calculado: ${total}'
                }, status=400)
            
            # Crear la venta
            venta = Venta.objects.create(
                numero_venta=numero_venta,
                usuario=request.user,
                subtotal=subtotal,
                descuento_total=descuento_total,
                total=total,
                estado='COMPLETADA'
            )

            # ============================================
            # PASO 3: CREAR DETALLES DE VENTA + CONSUMO FIFO
            # ============================================
            
            for item in carrito:
                producto = get_object_or_404(Producto, id=item['id'])
                cantidad = item['cantidad']
                precio_unitario = Decimal(str(item['precio_venta']))
                descuento_linea = Decimal(str(item.get('descuento', 0)))
                
                # Calcular subtotal de la línea
                subtotal_linea = cantidad * precio_unitario
                
                # Calcular porcentaje de descuento
                porcentaje_descuento = (descuento_linea / subtotal_linea * 100) if subtotal_linea > 0 else 0
                
                # Crear detalle de venta
                detalle = DetalleVenta.objects.create(
                    venta=venta,
                    producto=producto,
                    cantidad=cantidad,
                    precio_unitario=precio_unitario,
                    descuento_monto=descuento_linea,
                    descuento_porcentaje=porcentaje_descuento,
                    subtotal=subtotal_linea - descuento_linea
                )
                
                # ============================================
                # CONSUMIR STOCK FIFO
                # ============================================
                
                # Usar la función de fifo_logic.py
                resultado = procesar_venta_fifo(
                producto_id=producto.id,
                cantidad_solicitada=cantidad,
                venta_id=venta.id,
                usuario=request.user
            )

                # Verificar resultado
                if not resultado['success']:
                    print(f"⚠️ Error al procesar FIFO para {producto.nombre}")

                # Alertar si hay stock faltante (se permite venta igual)
                if resultado['cantidad_faltante'] > 0:
                    print(f"⚠️ ALERTA: Stock insuficiente - {producto.nombre}")
                    print(f"   Vendido: {resultado['cantidad_vendida']}")
                    print(f"   Faltante: {resultado['cantidad_faltante']}")
            
            # ============================================
            # PASO 4: REGISTRAR PAGOS
            # ============================================
            
            if metodo_pago == 'efectivo':
                # Solo efectivo
                Pago.objects.create(
                    venta=venta,
                    metodo='EFECTIVO',
                    monto=total,
                    referencia=f'Efectivo - {numero_venta}'
                )
            
            elif metodo_pago == 'transferencia':
                # Solo transferencia
                Pago.objects.create(
                    venta=venta,
                    metodo='TRANSFERENCIA',
                    monto=total,
                    referencia=f'Transferencia - {numero_venta}'
                )
            
            elif metodo_pago == 'mixto':
                # Efectivo
                if monto_efectivo > 0:
                    Pago.objects.create(
                        venta=venta,
                        metodo='EFECTIVO',
                        monto=monto_efectivo,
                        referencia=f'Efectivo (Mixto) - {numero_venta}'
                    )
                
                # Transferencia
                if monto_transferencia > 0:
                    Pago.objects.create(
                        venta=venta,
                        metodo='TRANSFERENCIA',
                        monto=monto_transferencia,
                        referencia=f'Transferencia (Mixto) - {numero_venta}'
                    )
            
            # ============================================
            # PASO 5: RETORNAR ÉXITO
            # ============================================
            # Asignar cliente si viene
            # Asignar cliente si viene
            cliente_id = data.get('cliente_id')
            if cliente_id:
                from apps.clientes.models import Cliente
                try:
                    venta.cliente = Cliente.objects.get(id=cliente_id, activo=True)
                except Cliente.DoesNotExist:
                    pass  # Si no existe, queda como contado (null)
            venta.save()

            if venta:
                resultado = print_manager.print_ticket_venta(
                    venta=venta,
                    usuario=request.user,
                    reimpresion=False
                )

                Auditoria.registrar_venta(
                    venta=venta,
                    usuario=request.user,
                    ip_address=get_client_ip(request)
                )


            return JsonResponse({
                'success': True,
                'venta': {
                    'id': venta.id,
                    'numero_venta': venta.numero_venta,
                    'total': float(venta.total),
                    'fecha': venta.fecha_venta.strftime('%d/%m/%Y %H:%M'),
                    'items_count': carrito.__len__(),
                },
                'mensaje': f'Venta {numero_venta} procesada exitosamente'
            })
    
    except Producto.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Uno o más productos no existen'
        }, status=404)
    
    except Exception as e:
        # Log del error
        print(f"❌ ERROR al procesar venta: {str(e)}")
        import traceback
        traceback.print_exc()
        
        return JsonResponse({
            'success': False,
            'error': f'Error al procesar la venta: {str(e)}'
        }, status=500)


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





