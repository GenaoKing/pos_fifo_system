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
from django.db.models import Sum
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

from django.db import transaction
from apps.sync import events as sync_events


def _encolar_compra_y_movimientos(compra):
    sync_events.evento_compra_registrada(compra)
    for detalle in compra.detalles.select_related('lote').all():
        lote = getattr(detalle, 'lote', None)
        if not lote:
            continue
        for movimiento in lote.movimientos.filter(referencia_tipo='Compra', referencia_id=compra.id):
            sync_events.evento_inventario_movimiento(movimiento)
    sync_events.evento_inventario_snapshot(sucursal=compra.sucursal)

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

                transaction.on_commit(lambda c=compra: _encolar_compra_y_movimientos(c))
                
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


# ============================================
# EDITAR UNA COMPRA (corrección de errores de captura)
# ============================================
#
# ⚠️ Integridad FIFO: una línea de compra (DetalleCompra) genera UN Lote.
# Solo se permite editar producto/cantidad de una línea mientras su lote esté
# INTACTO (nunca vendido ni ajustado). Si el lote ya tuvo movimiento, esas
# correcciones deben hacerse vía Ajustes de Inventario, pero el COSTO unitario
# sigue siendo editable: no toca cantidades y las ventas pasadas conservan su
# snapshot de costo_fifo (solo revalúa el stock restante y las ventas futuras).
# La cabecera (proveedor/factura/notas) siempre es editable porque no toca el
# FIFO.

def _estado_linea_compra(lote):
    """
    Determina si una línea de compra puede editarse según el estado de su lote.

    Un lote está "intacto" si: no se ha consumido (cantidad_actual ==
    cantidad_inicial), no tiene movimientos distintos del COMPRA inicial y no
    tiene ajustes de inventario.
    """
    if lote is None:
        return {'editable': False, 'costo_editable': False, 'vendido': 0,
                'motivo': 'La línea no tiene lote asociado.'}

    vendido = lote.cantidad_inicial - lote.cantidad_actual
    tiene_otros_mov = lote.movimientos.exclude(tipo='COMPRA').exists()
    tiene_ajustes = lote.ajustes.exists()

    if vendido > 0 or tiene_otros_mov or tiene_ajustes:
        if vendido > 0:
            motivo = (f'El lote ya tiene movimiento ({vendido} unidad(es) '
                      f'consumidas). Solo podés corregir el costo; el stock '
                      f'vía Ajustes de Inventario.')
        else:
            motivo = ('El lote ya registra ajustes/movimientos. Solo podés '
                      'corregir el costo; el stock vía Ajustes de Inventario.')
        return {'editable': False, 'costo_editable': True,
                'vendido': vendido, 'motivo': motivo}

    return {'editable': True, 'costo_editable': True, 'vendido': 0, 'motivo': ''}


def _snapshot_compra(compra):
    """Captura el estado de una compra para auditoría (antes/después)."""
    return {
        'proveedor': compra.proveedor,
        'numero_factura': compra.numero_factura,
        'notas': compra.notas,
        'total': str(compra.total),
        'lineas': [
            {
                'producto': d.producto.nombre,
                'producto_id': d.producto_id,
                'cantidad': d.cantidad,
                'costo_unitario': str(d.costo_unitario),
            }
            for d in compra.detalles.select_related('producto').all()
        ],
    }


def _validar_linea(cantidad, costo_unitario):
    if cantidad <= 0:
        raise ValueError('La cantidad debe ser mayor a 0.')
    if costo_unitario <= 0:
        raise ValueError('El costo unitario debe ser mayor a 0.')


@login_required
def compra_editar(request, compra_id):
    """
    Editar una compra existente para corregir errores de captura.

    GET  → formulario precargado (líneas con lote consumido salen bloqueadas).
    POST → aplica los cambios dentro de una transacción, propagando al Lote y
           al MovimientoLote(COMPRA) de cada línea intacta editada.
    """
    if not request.user.tiene_permiso('compras.registrar'):
        messages.error(request, 'No tienes permisos para editar compras.')
        return redirect('inventario:compras_lista')

    compra = get_object_or_404(Compra, id=compra_id)

    if request.method == 'GET':
        productos = Producto.objects.filter(activo=True).order_by('nombre')
        detalles = compra.detalles.select_related('producto', 'lote').all()

        lineas = []
        for d in detalles:
            lote = getattr(d, 'lote', None)
            estado = _estado_linea_compra(lote)
            lineas.append({
                'detalle_id': d.id,
                'producto_id': d.producto_id,
                'producto_nombre': d.producto.nombre,
                'cantidad': d.cantidad,
                'costo_unitario': float(d.costo_unitario),
                'numero_lote': lote.numero_lote if lote else None,
                'editable': estado['editable'],
                'costo_editable': estado['costo_editable'],
                'vendido': estado['vendido'],
                'motivo_bloqueo': estado['motivo'],
            })

        context = {
            'compra': compra,
            'productos': productos,
            'init_data_json': {
                'compra': {
                    'proveedor': compra.proveedor,
                    'numero_factura': compra.numero_factura or '',
                    'notas': compra.notas or '',
                },
                'lineas': lineas,
            },
        }
        return render(request, 'inventario/compra_editar.html', context)

    # ---- POST: aplicar cambios ----
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Datos inválidos.'}, status=400)

    proveedor = (data.get('proveedor') or '').strip()
    numero_factura = (data.get('numero_factura') or '').strip()
    notas = (data.get('notas') or '').strip()
    lineas_data = data.get('lineas', [])

    if not proveedor:
        return JsonResponse({'success': False, 'error': 'El proveedor es requerido'}, status=400)
    if not lineas_data:
        return JsonResponse({'success': False, 'error': 'La compra debe tener al menos un producto'}, status=400)

    try:
        with transaction.atomic():
            datos_anteriores = _snapshot_compra(compra)

            # Cabecera (siempre editable, no toca FIFO)
            compra.proveedor = proveedor
            compra.numero_factura = numero_factura or None
            compra.notas = notas or None

            detalles_existentes = {
                d.id: d for d in compra.detalles.select_related('lote', 'producto').all()
            }
            lineas_finales = 0

            for ln in lineas_data:
                detalle_id = ln.get('detalle_id')
                eliminar = bool(ln.get('eliminar'))
                producto_id = ln.get('producto_id')
                producto_id = int(producto_id) if producto_id not in (None, '') else None
                cantidad = int(ln.get('cantidad') or 0)
                costo_unitario = Decimal(str(ln.get('costo_unitario') or 0)).quantize(Decimal('0.01'))

                if detalle_id:
                    detalle = detalles_existentes.get(int(detalle_id))
                    if detalle is None:
                        raise ValueError(f'La línea {detalle_id} no pertenece a esta compra.')

                    lote = getattr(detalle, 'lote', None)
                    estado = _estado_linea_compra(lote)

                    cambio_estructural = (
                        eliminar
                        or producto_id != detalle.producto_id
                        or cantidad != detalle.cantidad
                    )
                    cambio_costo = costo_unitario != detalle.costo_unitario
                    cambio = cambio_estructural or cambio_costo

                    # Guarda de integridad: en líneas con lote consumido solo
                    # se permite corregir el costo (no toca cantidades FIFO)
                    if not estado['editable']:
                        if cambio_estructural or (cambio_costo and not estado['costo_editable']):
                            raise ValueError(
                                f'La línea "{detalle.producto.nombre}" no se puede modificar: '
                                f'{estado["motivo"]}'
                            )
                        if cambio_costo:
                            if costo_unitario <= 0:
                                raise ValueError('El costo unitario debe ser mayor a 0.')
                            detalle.costo_unitario = costo_unitario
                            detalle._lote_creado = True  # evita que save() genere un lote nuevo
                            detalle.save()
                            lote.costo_unitario = costo_unitario
                            lote.save()
                        lineas_finales += 1
                        continue

                    if eliminar:
                        if lote is not None:
                            lote.delete()  # CASCADE borra sus MovimientoLote
                        detalle.delete()
                        continue

                    if not cambio:
                        lineas_finales += 1
                        continue

                    # Línea intacta con cambios → propagar a detalle + lote + movimiento
                    _validar_linea(cantidad, costo_unitario)
                    producto = Producto.objects.get(id=producto_id)

                    detalle.producto = producto
                    detalle.cantidad = cantidad
                    detalle.costo_unitario = costo_unitario
                    detalle._lote_creado = True  # evita que save() genere un lote nuevo
                    detalle.save()

                    if lote is not None:
                        lote.producto = producto
                        lote.cantidad_inicial = cantidad
                        lote.cantidad_actual = cantidad
                        lote.costo_unitario = costo_unitario
                        lote.save()

                        mov = lote.movimientos.filter(tipo='COMPRA').first()
                        if mov:
                            mov.cantidad = cantidad
                            mov.cantidad_nueva = cantidad
                            mov.notas = f'Compra corregida - {compra.numero_compra}'
                            mov.save()

                    lineas_finales += 1
                else:
                    # Nueva línea → DetalleCompra.save() auto-genera el lote
                    if eliminar:
                        continue
                    _validar_linea(cantidad, costo_unitario)
                    producto = Producto.objects.get(id=producto_id)
                    DetalleCompra.objects.create(
                        compra=compra,
                        producto=producto,
                        cantidad=cantidad,
                        costo_unitario=costo_unitario,
                        subtotal=cantidad * costo_unitario,
                    )
                    asignar_codigo_si_vacio(producto)
                    lineas_finales += 1

            if lineas_finales == 0:
                raise ValueError('La compra debe conservar al menos un producto.')

            # Recalcular total desde los detalles actuales
            compra.total = compra.detalles.aggregate(t=Sum('subtotal'))['t'] or Decimal('0')
            compra.save()

            Auditoria.registrar(
                accion=Auditoria.TipoAccion.EDITAR,
                descripcion=f'Compra {compra.numero_compra} editada (corrección)',
                usuario=request.user,
                content_object=compra,
                datos_anteriores=datos_anteriores,
                datos_nuevos=_snapshot_compra(compra),
                ip_address=get_client_ip(request),
                nivel_importancia='ALTA',
                sucursal=getattr(request, 'sucursal', None),
            )

            transaction.on_commit(lambda c=compra: _encolar_compra_y_movimientos(c))

        return JsonResponse({
            'success': True,
            'message': 'Compra actualizada correctamente.',
            'compra_id': compra.id,
            'total': float(compra.total),
        })

    except Producto.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Uno de los productos seleccionados no existe'}, status=400)
    except ValueError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Error al actualizar la compra: {str(e)}'}, status=500)


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
    if not request.user.tiene_permiso('inventario.ajustar'):
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
    if not request.user.tiene_permiso('inventario.ajustar'):
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
            # NOTA(bug preexistente): AjusteInventario.save() YA crea un
            # MovimientoLote y ajusta el lote, por lo que esta creacion manual
            # genera un segundo movimiento por cada ajuste (el stock no se
            # duplica, pero el ledger si). La variable `movimiento` queda sin
            # uso. Pendiente eliminar esta duplicacion tras validar reportes.
            movimiento = MovimientoLote.objects.create(
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
            
            transaction.on_commit(lambda a=ajuste: sync_events.evento_ajuste_inventario(a))
            transaction.on_commit(lambda s=lote.sucursal: sync_events.evento_inventario_snapshot(sucursal=s))

 
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
