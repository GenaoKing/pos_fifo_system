"""
Views para Gestión de Compras
apps/inventario/views.py

Este módulo maneja:
1. Lista de compras históricas
2. Formulario para registrar nuevas compras
3. Auto-generación de lotes FIFO
"""

from apps.configuracion.decorators import requiere_modulo
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from decimal import Decimal
from datetime import datetime

from apps.inventario.models import Compra, DetalleCompra, Lote
from apps.productos.models import Producto
from apps.productos.utils import asignar_codigo_si_vacio
from utils.impresoras.zebra import imprimir_etiquetas_compra


# ============================================
# LISTA DE COMPRAS
# ============================================

@login_required
def compras_lista(request):
    """
    Muestra historial de compras realizadas.
    Solo Admin puede ver esta vista.
    """
    # Verificar que sea Admin
    if not request.user.es_admin:
        messages.error(request, 'No tienes permisos para acceder a esta sección.')
        return redirect('dashboard')
    
    # Obtener todas las compras ordenadas por fecha (más reciente primero)
    compras = Compra.objects.all().select_related('usuario').order_by('-fecha_compra')
    
    context = {
        'compras': compras,
    }
    return render(request, 'inventario/compras_lista.html', context)


# ============================================
# FORMULARIO DE NUEVA COMPRA
# ============================================

@login_required
def compra_crear(request):
    """
    Formulario para crear una nueva compra.
    Permite agregar múltiples productos en una sola compra.
    """
    # Verificar que sea Admin
    if not request.user.es_admin:
        messages.error(request, 'No tienes permisos para crear compras.')
        return redirect('dashboard')
    
    if request.method == 'GET':
        # Mostrar formulario vacío
        # Obtener todos los productos activos para el selector
        productos = Producto.objects.filter(activo=True).order_by('nombre')
        
        context = {
            'productos': productos,
        }
        return render(request, 'inventario/compra_crear.html', context)
    
    elif request.method == 'POST':
        """
        Procesar la compra enviada desde el formulario.
        
        Datos esperados (JSON en POST):
        {
            "proveedor": "Nombre del proveedor",
            "numero_factura": "FAC-001",
            "productos": [
                {
                    "producto_id": 1,
                    "cantidad": 50,
                    "costo_unitario": 10.50
                },
                {
                    "producto_id": 2,
                    "cantidad": 30,
                    "costo_unitario": 25.00
                }
            ]
        }
        """
        
        try:
            import json
            
            # Parsear los datos JSON del POST
            data = json.loads(request.body)
            
            proveedor = data.get('proveedor', '').strip()
            numero_factura = data.get('numero_factura', '').strip()
            productos_data = data.get('productos', [])
            
            # Validaciones básicas
            if not proveedor:
                return JsonResponse({
                    'success': False,
                    'error': 'El proveedor es requerido'
                }, status=400)
            
            if not productos_data or len(productos_data) == 0:
                return JsonResponse({
                    'success': False,
                    'error': 'Debe agregar al menos un producto'
                }, status=400)
            
            # Usar transacción para que todo se guarde o nada
            # (si algo falla, se revierte todo)
            with transaction.atomic():
                
                # 1. Crear la Compra (cabecera)
                compra = Compra.objects.create(
                    usuario=request.user,
                    proveedor=proveedor,
                    numero_factura=numero_factura if numero_factura else None,
                    total=Decimal('0')  # Lo calculamos después
                )
                
                total_compra = Decimal('0')
                lotes_creados = []
                
                # 2. Procesar cada producto de la compra
                for item in productos_data:
                    producto_id = item.get('producto_id')
                    cantidad = int(item.get('cantidad', 0))
                    costo_unitario = Decimal(str(item.get('costo_unitario', 0)))
                    
                    # Validar que exista el producto
                    producto = Producto.objects.get(id=producto_id)
                    
                    # Calcular subtotal de esta línea
                    subtotal = cantidad * costo_unitario
                    total_compra += subtotal
                    
                    # 3. Crear el DetalleCompra (línea de la compra)
                    detalle = DetalleCompra.objects.create(
                        compra=compra,
                        producto=producto,
                        cantidad=cantidad,
                        costo_unitario=costo_unitario,
                        subtotal=subtotal
                    )

                    # Asignar código de barras interno si el producto no tiene uno
                    asignar_codigo_si_vacio(producto)

                    # 4. Auto-generar Lote FIFO
                    # Cada detalle de compra genera UN lote independiente

                
                # 5. Actualizar el total de la compra
                compra.total = total_compra
                compra.save()
                
                # Respuesta exitosa
                return JsonResponse({
                    'success': True,
                    'message': f'Compra registrada exitosamente.',
                    'compra_id': compra.id,
                    'numero_compra': compra.numero_compra,
                    'total': float(total_compra),
                    #'lotes': lotes_creados
                })
        
        except Producto.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': 'Uno de los productos seleccionados no existe'
            }, status=400)
        
        except Exception as e:
            # Si algo sale mal, devolver error
            return JsonResponse({
                'success': False,
                'error': f'Error al procesar la compra: {str(e)}'
            }, status=500)


# ============================================
# API: BUSCAR PRODUCTOS (para autocompletado)
# ============================================

@login_required
@require_http_methods(["GET"])
def productos_buscar(request):
    """
    API para buscar productos por nombre o SKU.
    Usado en el autocompletado del formulario de compras.
    
    Query params:
        q: término de búsqueda
    
    Retorna:
        JSON con lista de productos encontrados
    """
    query = request.GET.get('q', '').strip()
    
    if len(query) < 2:
        return JsonResponse({'productos': []})
    
    # Buscar productos que coincidan
    productos = Producto.objects.filter(
        activo=True
    ).filter(
        # Buscar por nombre o SKU
        nombre__icontains=query
    ) | Producto.objects.filter(
        activo=True,
        sku__icontains=query
    )
    
    # Limitar a 10 resultados
    productos = productos[:10]
    
    # Convertir a JSON
    data = {
        'productos': [
            {
                'id': p.id,
                'nombre': p.nombre,
                'sku': p.sku,
                'precio_venta': float(p.precio_venta),
                'stock_actual': p.obtener_stock_total()  # Método del modelo
            }
            for p in productos
        ]
    }
    
    return JsonResponse(data)


# ============================================
# DETALLE DE UNA COMPRA
# ============================================

@login_required
def compra_detalle(request, compra_id):
    """
    Muestra el detalle completo de una compra específica.
    Incluye todos los productos y lotes generados.
    """
    # Verificar que sea Admin
    if not request.user.es_admin:
        messages.error(request, 'No tienes permisos para ver esta información.')
        return redirect('dashboard')
    
    # Obtener la compra
    compra = get_object_or_404(Compra, id=compra_id)
    
    # Obtener los detalles (líneas de productos)
    detalles = compra.detalles.all().select_related('producto','lote')
    
    # Obtener los lotes generados por esta compra
    lotes = Lote.objects.filter(
        detalle_compra__compra=compra
    ).select_related('producto', 'detalle_compra')
    
    context = {
        'compra': compra,
        'detalles': detalles,
        'lotes': lotes,
    }
    
    return render(request, 'inventario/compra_detalle.html', context)


@login_required
@require_http_methods(["POST"])
@requiere_modulo('etiquetas_zebra')
def compra_imprimir_etiquetas(request, compra_id):
    """
    Imprime etiquetas de todos los productos con código interno de una compra
    """
    # Verificar que sea Admin
    if not request.user.es_admin:
        return JsonResponse({
            'success': False,
            'error': 'No tienes permisos para esta acción'
        }, status=403)
    
    try:
        compra = get_object_or_404(Compra, id=compra_id)
        
        # Imprimir etiquetas
        resultado = imprimir_etiquetas_compra(compra)
        
        if resultado['success']:
            messages.success(
                request, 
                f'Se imprimieron {resultado["total_etiquetas"]} etiquetas de la compra {compra.numero_compra}'
            )
        else:
            messages.error(request, f'Error: {resultado.get("error", "Error desconocido")}')
        
        return JsonResponse(resultado)
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)