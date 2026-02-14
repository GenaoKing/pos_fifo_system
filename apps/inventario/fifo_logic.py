"""
apps/inventario/fifo_logic.py
⭐ Lógica FIFO Core del Sistema
"""
from django.db import transaction
from django.db.models import Sum
from .models import Lote, MovimientoLote
from decimal import Decimal


def obtener_stock_disponible(producto_id):
    """
    Stock total de un producto (suma de todos sus lotes activos)
    
    Args:
        producto_id: ID del producto
        
    Returns:
        int: Cantidad total disponible
    """
    total = Lote.objects.filter(
        producto_id=producto_id,
        cantidad_actual__gt=0,
        activo=True
    ).aggregate(
        total=Sum('cantidad_actual')
    )['total']
    
    return total or 0


def obtener_lotes_fifo(producto_id, cantidad_necesaria):
    """
    Obtiene los lotes que se deben consumir siguiendo FIFO
    
    Args:
        producto_id: ID del producto
        cantidad_necesaria: Cantidad a consumir
        
    Returns:
        list: Lista con info de consumo por lote
    """
    # Obtener lotes ordenados por fecha (más antiguo primero)
    lotes = Lote.objects.filter(
        producto_id=producto_id,
        cantidad_actual__gt=0,
        activo=True
    ).order_by('fecha_compra', 'id')
    
    resultado = []
    cantidad_pendiente = cantidad_necesaria
    
    for lote in lotes:
        if cantidad_pendiente <= 0:
            break
        
        # Tomar lo menor entre disponible y pendiente
        cantidad_a_tomar = min(lote.cantidad_actual, cantidad_pendiente)
        
        resultado.append({
            'lote': lote,
            'cantidad': cantidad_a_tomar,
            'costo_unitario': lote.costo_unitario,
            'subtotal_costo': cantidad_a_tomar * lote.costo_unitario
        })
        
        cantidad_pendiente -= cantidad_a_tomar
    
    return resultado


@transaction.atomic
def procesar_venta_fifo(producto_id, cantidad_solicitada, venta_id, usuario):
    """
    ⭐ FUNCIÓN CORE - Procesa venta consumiendo lotes en orden FIFO
    
    Args:
        producto_id: ID del producto
        cantidad_solicitada: Cantidad a vender
        venta_id: ID de la venta
        usuario: Usuario que realiza la venta
        
    Returns:
        dict: Información del procesamiento
    """
    # Obtener lotes a consumir
    lotes_consumo = obtener_lotes_fifo(producto_id, cantidad_solicitada)
    
    cantidad_disponible = sum(item['cantidad'] for item in lotes_consumo)
    
    movimientos_creados = []
    costo_total_fifo = Decimal('0.00')
    
    # Procesar cada lote
    for item in lotes_consumo:
        lote = item['lote']
        cantidad = item['cantidad']
        
        cantidad_anterior = lote.cantidad_actual
        
        # Actualizar lote
        lote.cantidad_actual -= cantidad
        lote.save()
        
        # Crear movimiento
        movimiento = MovimientoLote.objects.create(
            lote=lote,
            tipo='VENTA',
            cantidad=-cantidad,  # Negativo = salida
            cantidad_anterior=cantidad_anterior,
            cantidad_nueva=lote.cantidad_actual,
            referencia_tipo='Venta',
            referencia_id=venta_id,
            usuario=usuario,
            notas=f'Venta #{venta_id}'
        )
        
        movimientos_creados.append(movimiento)
        costo_total_fifo += item['subtotal_costo']
    
    stock_faltante = cantidad_solicitada - cantidad_disponible
    
    return {
        'success': True,
        'cantidad_vendida': cantidad_disponible,
        'cantidad_faltante': stock_faltante,
        'tiene_stock_completo': stock_faltante == 0,
        'movimientos': movimientos_creados,
        'costo_fifo': costo_total_fifo,
        'lotes_consumidos': len(lotes_consumo)
    }


@transaction.atomic
def anular_venta_devolver_stock(venta_id, usuario):
    """
    Anula venta y devuelve stock a los lotes originales
    
    Args:
        venta_id: ID de la venta
        usuario: Usuario que anula
        
    Returns:
        dict: Resultado de la operación
    """
    # Obtener movimientos de la venta
    movimientos = MovimientoLote.objects.filter(
        referencia_tipo='Venta',
        referencia_id=venta_id,
        tipo='VENTA'
    )
    
    if not movimientos.exists():
        return {
            'success': False,
            'error': 'No se encontraron movimientos'
        }
    
    lotes_actualizados = []
    
    # Devolver stock a cada lote
    for movimiento in movimientos:
        lote = movimiento.lote
        cantidad_devolver = abs(movimiento.cantidad)
        
        cantidad_anterior = lote.cantidad_actual
        
        # Devolver stock
        lote.cantidad_actual += cantidad_devolver
        lote.save()
        
        # Crear movimiento de anulación
        MovimientoLote.objects.create(
            lote=lote,
            tipo='ANULACION',
            cantidad=cantidad_devolver,  # Positivo = entrada
            cantidad_anterior=cantidad_anterior,
            cantidad_nueva=lote.cantidad_actual,
            referencia_tipo='AnulacionVenta',
            referencia_id=venta_id,
            usuario=usuario,
            notas=f'Anulación de venta #{venta_id}'
        )
        
        lotes_actualizados.append(lote)
    
    return {
        'success': True,
        'lotes_actualizados': len(lotes_actualizados),
        'cantidad_devuelta': sum(abs(m.cantidad) for m in movimientos)
    }


def calcular_valuacion_fifo(producto_id=None):
    """
    Calcula valor del inventario usando FIFO
    
    Args:
        producto_id: ID del producto (None = todos)
        
    Returns:
        Decimal: Valor total
    """
    filtro = {'cantidad_actual__gt': 0, 'activo': True}
    
    if producto_id:
        filtro['producto_id'] = producto_id
    
    lotes = Lote.objects.filter(**filtro)
    
    valor_total = sum(
        lote.cantidad_actual * lote.costo_unitario 
        for lote in lotes
    )
    
    return Decimal(str(valor_total))


def verificar_stock_minimo(producto):
    """
    Verifica si producto está bajo stock mínimo
    
    Args:
        producto: Instancia de Producto
        
    Returns:
        dict: Estado del stock
    """
    stock_actual = obtener_stock_disponible(producto.id)
    
    return {
        'producto_id': producto.id,
        'producto_nombre': producto.nombre,
        'stock_actual': stock_actual,
        'stock_minimo': producto.stock_minimo,
        'bajo_minimo': stock_actual < producto.stock_minimo,
        'diferencia': stock_actual - producto.stock_minimo
    }


def obtener_productos_bajo_stock():
    """
    Lista de productos bajo stock mínimo
    
    Returns:
        list: Productos con stock bajo
    """
    from apps.productos.models import Producto
    
    productos = Producto.objects.filter(activo=True)
    productos_bajo_stock = []
    
    for producto in productos:
        stock = obtener_stock_disponible(producto.id)
        if stock < producto.stock_minimo:
            productos_bajo_stock.append({
                'producto': producto,
                'stock_actual': stock,
                'stock_minimo': producto.stock_minimo,
                'faltante': producto.stock_minimo - stock
            })
    
    return productos_bajo_stock