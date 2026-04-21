"""
Views para Gestión de Compras
apps/inventario/views.py

Este módulo maneja:
1. Lista de compras históricas
2. Formulario para registrar nuevas compras
3. Auto-generación de lotes FIFO
"""

import json

from apps.configuracion.decorators import requiere_modulo
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from decimal import Decimal
from datetime import datetime

from apps.inventario.models import AjusteInventario, Compra, DetalleCompra, Lote, MovimientoLote
from apps.productos.models import Producto
from apps.productos.utils import asignar_codigo_si_vacio
from apps.auditoria.models import Auditoria, get_client_ip
from apps.inventario.fifo_logic import obtener_stock_disponible, obtener_lotes_fifo 
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
    #if not request.user.es_admin:
     #   messages.error(request, 'No tienes permisos para acceder a esta sección.')
      #  return redirect('reportes:dashboard')
    
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
    #if not request.user.es_admin:
     #   messages.error(request, 'No tienes permisos para crear compras.')
      #  return redirect('reportes:dashboard')
    
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
                    sucursal=request.sucursal,
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
                'stock_actual': obtener_stock_disponible(p)  # Método del modelo
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
    #if not request.user.es_admin:
     #   messages.error(request, 'No tienes permisos para ver esta información.')
      #  return redirect('reportes:dashboard')
    
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
    #if not request.user.es_admin:
    #    return JsonResponse({
     #       'success': False,
      #      'error': 'No tienes permisos para esta acción'
       # }, status=403)
    
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
    



@login_required
def vista_ajustes(request):
    """
    Página para realizar ajustes de inventario.
    Solo accesible por ADMIN y SYSADMIN.
    """
    if request.user.rol not in ('ADMIN', 'SYSADMIN'):
        messages.error(request, 'No tienes permisos para acceder a esta sección.')
        return redirect('pos:punto_venta')
 
    # Historial de últimos 50 ajustes
    ajustes_recientes = AjusteInventario.objects.select_related(
        'lote', 'lote__producto', 'usuario'
    ).order_by('-fecha_ajuste')[:50]
 
    ajustes_data = []
    for a in ajustes_recientes:
        ajustes_data.append({
            'id': a.id,
            'fecha': a.fecha_ajuste.strftime('%d/%m/%Y %H:%M'),
            'producto': a.lote.producto.nombre,
            'lote': a.lote.numero_lote,
            'tipo': a.tipo,
            'tipo_display': a.get_tipo_display(),
            'cantidad': a.cantidad,
            'motivo': a.motivo,
            'usuario': a.usuario.get_full_name() or a.usuario.username,
        })
 
    tipos_ajuste = [
        {'value': 'MERMA', 'label': 'Merma', 'desc': 'Pérdida por deterioro natural'},
        {'value': 'DANO', 'label': 'Daño', 'desc': 'Producto dañado o roto'},
        {'value': 'CONTEO', 'label': 'Ajuste por Conteo', 'desc': 'Corrección por conteo físico'},
        {'value': 'CORRECCION', 'label': 'Corrección', 'desc': 'Corrección de error de registro'},
        {'value': 'DEVOLUCION', 'label': 'Devolución', 'desc': 'Devolución de producto'},
    ]
 
    context = {
        'init_data_json': {
            'ajustes_recientes': ajustes_data,
            'tipos_ajuste': tipos_ajuste,
        },
    }
 
    return render(request, 'inventario/ajustes.html', context)
 
 
# ============================================
# API: LOTES DE UN PRODUCTO
# ============================================
 
@login_required
@require_http_methods(["GET"])
def api_lotes_producto(request, producto_id):
    """
    Retorna los lotes con stock disponible de un producto.
 
    GET /inventario/api/lotes/<producto_id>/
 
    Returns:
        JSON con lista de lotes y datos del producto
    """
    try:
        producto = get_object_or_404(Producto, id=producto_id, activo=True)
 
        lotes = Lote.objects.filter(
            producto=producto,
            activo=True,
        ).order_by('fecha_compra', 'id')
 
        lotes_data = []
        for lote in lotes:
            lotes_data.append({
                'id': lote.id,
                'numero_lote': lote.numero_lote,
                'fecha_compra': lote.fecha_compra.strftime('%d/%m/%Y'),
                'cantidad_inicial': lote.cantidad_inicial,
                'cantidad_actual': lote.cantidad_actual,
                'costo_unitario': str(lote.costo_unitario),
                'valor_actual': str(lote.get_valor_actual()),
                'agotado': lote.esta_agotado(),
            })
 
        return JsonResponse({
            'success': True,
            'producto': {
                'id': producto.id,
                'nombre': producto.nombre,
                'sku': producto.sku,
            },
            'lotes': lotes_data,
        })
 
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)
 
 
# ============================================
# API: PROCESAR AJUSTE DE INVENTARIO
# ============================================
 
@login_required
@require_http_methods(["POST"])
def api_ajustar_inventario(request):
    """
    Procesa un ajuste de inventario.
 
    POST Body (JSON):
    {
        "lote_id": 123,
        "tipo": "MERMA",       // MERMA|DANO|CONTEO|CORRECCION|DEVOLUCION
        "cantidad": 5,         // Siempre positivo, se convierte a negativo
        "motivo": "Texto..."   // Obligatorio, min 10 chars
    }
 
    Flujo:
    1. Valida permisos y datos
    2. Verifica que el lote tenga stock suficiente (excepto DEVOLUCION)
    3. Crea AjusteInventario
    4. Crea MovimientoLote
    5. Actualiza lote.cantidad_actual
    6. Registra en Auditoría
    """
    if request.user.rol not in ('ADMIN', 'SYSADMIN'):
        return JsonResponse({
            'success': False,
            'error': 'No tienes permisos para realizar ajustes.'
        }, status=403)
 
    try:
        data = json.loads(request.body)
        lote_id = data.get('lote_id')
        tipo = data.get('tipo', '')
        cantidad = int(data.get('cantidad', 0))
        motivo = data.get('motivo', '').strip()
 
        # Validaciones
        tipos_validos = ['MERMA', 'DANO', 'CONTEO', 'CORRECCION', 'DEVOLUCION']
        if tipo not in tipos_validos:
            return JsonResponse({
                'success': False,
                'error': f'Tipo de ajuste inválido. Opciones: {", ".join(tipos_validos)}'
            }, status=400)
 
        if cantidad <= 0:
            return JsonResponse({
                'success': False,
                'error': 'La cantidad debe ser mayor a cero.'
            }, status=400)
 
        if not motivo or len(motivo) < 10:
            return JsonResponse({
                'success': False,
                'error': 'El motivo es obligatorio (mínimo 10 caracteres).'
            }, status=400)
 
        lote = get_object_or_404(Lote, id=lote_id, activo=True)
 
        # Determinar dirección del ajuste
        # DEVOLUCION agrega stock, los demás lo quitan
        es_entrada = tipo == 'DEVOLUCION'
        cantidad_ajuste = cantidad if es_entrada else -cantidad
 
        # Validar stock suficiente para salidas
        if not es_entrada and lote.cantidad_actual < cantidad:
            return JsonResponse({
                'success': False,
                'error': f'Stock insuficiente. El lote tiene {lote.cantidad_actual} unidades disponibles.'
            }, status=400)
 
        # Mapear tipo ajuste a tipo movimiento
        tipo_movimiento_map = {
            'MERMA': 'MERMA',
            'DANO': 'DANO',
            'CONTEO': 'AJUSTE',
            'CORRECCION': 'AJUSTE',
            'DEVOLUCION': 'AJUSTE',
        }
 
        with transaction.atomic():
            cantidad_anterior = lote.cantidad_actual
            cantidad_nueva = lote.cantidad_actual + cantidad_ajuste
 
            # 1. Crear AjusteInventario
            ajuste = AjusteInventario.objects.create(
                lote=lote,
                tipo=tipo,
                cantidad=cantidad_ajuste,
                motivo=motivo,
                usuario=request.user,
            )
 
            # 2. Crear MovimientoLote
            MovimientoLote.objects.create(
                lote=lote,
                tipo=tipo_movimiento_map[tipo],
                cantidad=cantidad_ajuste,
                cantidad_anterior=cantidad_anterior,
                cantidad_nueva=cantidad_nueva,
                referencia_tipo='AjusteInventario',
                referencia_id=ajuste.id,
                usuario=request.user,
                notas=f'{ajuste.get_tipo_display()}: {motivo}',
            )
 
            # 3. Actualizar lote
            lote.cantidad_actual = cantidad_nueva
            lote.save()
 
            # 4. Registrar en auditoría
            Auditoria.registrar_ajuste_inventario(
                ajuste=ajuste,
                usuario=request.user,
                ip_address=get_client_ip(request),
            )
 
        return JsonResponse({
            'success': True,
            'message': f'Ajuste registrado: {ajuste.get_tipo_display()} de {cantidad} unidades.',
            'ajuste': {
                'id': ajuste.id,
                'producto': lote.producto.nombre,
                'lote': lote.numero_lote,
                'tipo': tipo,
                'tipo_display': ajuste.get_tipo_display(),
                'cantidad': cantidad_ajuste,
                'cantidad_anterior': cantidad_anterior,
                'cantidad_nueva': cantidad_nueva,
                'motivo': motivo,
                'fecha': ajuste.fecha_ajuste.strftime('%d/%m/%Y %H:%M'),
                'usuario': request.user.get_full_name() or request.user.username,
            }
        })
 
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False, 'error': 'Datos inválidos.'
        }, status=400)
    except ValueError:
        return JsonResponse({
            'success': False, 'error': 'La cantidad debe ser un número entero.'
        }, status=400)
    except Exception as e:
        print(f"❌ ERROR en ajuste de inventario: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False, 'error': f'Error inesperado: {str(e)}'
        }, status=500)