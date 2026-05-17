from django.db import transaction
from django.db.models import Sum, Count, Avg
from decimal import Decimal
from datetime import date

from .models import CierreCaja, TopProducto, InventarioValorizado
from apps.ventas.models import Venta, DetalleVenta, Pago
from apps.productos.models import Producto
from apps.inventario.models import Lote


class ReporteManager:
    """
    Maneja la generación de todos los reportes del sistema
    """
    
    @staticmethod
    @transaction.atomic
    def generar_cierre_diario(fecha=None, generado_automaticamente=False, usuario=None):
        """
        Genera el cierre de caja para una fecha específica
        """
        if fecha is None:
            from django.utils import timezone
            fecha = timezone.localdate()
        
        # Verificar si ya existe
        cierre_existente = CierreCaja.objects.filter(fecha=fecha).first()
        if cierre_existente:
            return cierre_existente
        
        # Obtener ventas del día
        ventas = Venta.objects.filter(
            fecha_venta__date=fecha,
            estado='COMPLETADA'
        )
        
        # Calcular totales
        totales_ventas = ventas.aggregate(
            cantidad=Count('id'),
            total=Sum('total'),
            descuentos=Sum('descuento_total')
        )
        
        # Totales por método de pago
        pagos_efectivo = Pago.objects.filter(
            venta__in=ventas,
            metodo='EFECTIVO'
        ).aggregate(total=Sum('monto'))['total'] or Decimal('0.00')
        
        pagos_transferencia = Pago.objects.filter(
            venta__in=ventas,
            metodo='TRANSFERENCIA'
        ).aggregate(total=Sum('monto'))['total'] or Decimal('0.00')
        
        pagos_tarjeta = Pago.objects.filter(
            venta__in=ventas,
            metodo='TARJETA'
        ).aggregate(total=Sum('monto'))['total'] or Decimal('0.00')
        
        # Anulaciones
        anulaciones = Venta.objects.filter(
            fecha_anulacion__date=fecha,
            estado='ANULADA'
        ).aggregate(
            cantidad=Count('id'),
            total=Sum('total')
        )
        
        # Resumen por cajero
        resumen_cajeros = {}
        for venta in ventas.select_related('usuario'):
            cajero_id = str(venta.usuario.id)
            
            if cajero_id not in resumen_cajeros:
                resumen_cajeros[cajero_id] = {
                    'nombre': venta.usuario.get_full_name() or venta.usuario.username,
                    'cantidad': 0,
                    'total': Decimal('0.00')
                }
            
            resumen_cajeros[cajero_id]['cantidad'] += 1
            resumen_cajeros[cajero_id]['total'] += venta.total
        
        # Convertir Decimals a strings para JSON
        for cajero_id in resumen_cajeros:
            resumen_cajeros[cajero_id]['total'] = str(
                resumen_cajeros[cajero_id]['total']
            )
        
        # Crear el cierre
        cierre = CierreCaja.objects.create(
            fecha=fecha,
            cantidad_ventas=totales_ventas['cantidad'] or 0,
            total_ventas=totales_ventas['total'] or Decimal('0.00'),
            total_descuentos=totales_ventas['descuentos'] or Decimal('0.00'),
            total_efectivo=pagos_efectivo,
            total_transferencia=pagos_transferencia,
            total_tarjeta=pagos_tarjeta,
            cantidad_anulaciones=anulaciones['cantidad'] or 0,
            total_anulaciones=anulaciones['total'] or Decimal('0.00'),
            resumen_cajeros=resumen_cajeros,
            generado_automaticamente=generado_automaticamente,
            generado_por=usuario,
            cerrado=True
        )
        
        return cierre
    
    @staticmethod
    @transaction.atomic
    def generar_top_productos(fecha_inicio, fecha_fin, limite=10):
        """
        Genera ranking de productos más vendidos
        """
        # Limpiar anteriores
        TopProducto.objects.filter(
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin
        ).delete()
        
        # Obtener ventas del período
        ventas = Venta.objects.filter(
            fecha_venta__date__gte=fecha_inicio,
            fecha_venta__date__lte=fecha_fin,
            estado='COMPLETADA'
        )
        
        # Agrupar por producto
        detalles = DetalleVenta.objects.filter(
            venta__in=ventas
        ).values('producto').annotate(
            cantidad_vendida=Sum('cantidad'),
            total_ventas=Sum('total'),
            numero_transacciones=Count('venta', distinct=True)
        ).order_by('-cantidad_vendida')[:limite]
        
        top_productos = []
        
        for detalle in detalles:
            producto = Producto.objects.get(id=detalle['producto'])
            
            # Margen promedio simple (puedes mejorarlo con FIFO real después)
            margen_promedio = Decimal('25.0')  # Placeholder
            
            top = TopProducto.objects.create(
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
                producto=producto,
                cantidad_vendida=detalle['cantidad_vendida'],
                total_ventas=detalle['total_ventas'],
                numero_transacciones=detalle['numero_transacciones'],
                margen_promedio=margen_promedio
            )
            
            top_productos.append(top)
        
        return top_productos
    
    @staticmethod
    @transaction.atomic
    def generar_inventario_valorizado(fecha=None):
        """
        Genera snapshot del inventario valorizado según FIFO
        """
        if fecha is None:
            from django.utils import timezone
            fecha = timezone.localdate()
        
        # Verificar si ya existe
        inventario_existente = InventarioValorizado.objects.filter(
            fecha=fecha
        ).first()
        if inventario_existente:
            return inventario_existente
        
        # Obtener productos activos
        productos = Producto.objects.filter(activo=True)
        
        datos_inventario = []
        total_unidades = Decimal('0.00')
        valor_total = Decimal('0.00')
        
        for producto in productos:
            # Obtener lotes activos
            lotes = Lote.objects.filter(
                producto=producto,
                cantidad_actual__gt=0,
                activo=True
            ).order_by('fecha_compra')
            
            if not lotes.exists():
                continue
            
            # Calcular totales
            cantidad_total = sum(l.cantidad_actual for l in lotes)
            valor_producto = sum(
                l.cantidad_actual * l.costo_unitario for l in lotes
            )
            costo_promedio = valor_producto / cantidad_total if cantidad_total > 0 else Decimal('0.00')
            
            # Detalles por lote
            lotes_data = []
            for lote in lotes:
                valor_lote = lote.cantidad_actual * lote.costo_unitario
                lotes_data.append({
                    'numero_lote': lote.numero_lote,
                    'cantidad': str(lote.cantidad_actual),
                    'costo_unitario': str(lote.costo_unitario),
                    'valor_lote': str(valor_lote),
                    'fecha_compra': lote.fecha_compra.isoformat()
                })
            
            datos_inventario.append({
                'producto_id': producto.id,
                'nombre': producto.nombre,
                'sku': producto.sku,
                'cantidad_total': str(cantidad_total),
                'costo_promedio_fifo': str(costo_promedio),
                'valor_total': str(valor_producto),
                'lotes': lotes_data
            })
            
            total_unidades += cantidad_total
            valor_total += valor_producto
        
        # Crear el inventario
        inventario = InventarioValorizado.objects.create(
            fecha=fecha,
            datos_inventario=datos_inventario,
            total_productos=len(datos_inventario),
            total_unidades=total_unidades,
            valor_total_inventario=valor_total
        )
        
        return inventario
