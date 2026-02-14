from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Sum, Count
from .models import Categoria, Producto


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    """Administración de categorías"""
    
    list_display = (
        'nombre', 
        'total_productos', 
        'productos_activos',
        'activa', 
        'fecha_creacion'
    )
    list_filter = ('activa', 'fecha_creacion')
    search_fields = ('nombre', 'descripcion')
    ordering = ('nombre',)
    
    fieldsets = (
        ('Información básica', {
            'fields': ('nombre', 'descripcion', 'activa')
        }),
        ('Información del sistema', {
            'fields': ('fecha_creacion', 'fecha_modificacion'),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ('fecha_creacion', 'fecha_modificacion')
    
    def total_productos(self, obj):
        """Total de productos en la categoría"""
        return obj.productos.count()
    total_productos.short_description = 'Total productos'
    
    def productos_activos(self, obj):
        """Productos activos en la categoría"""
        count = obj.productos.filter(activo=True).count()
        return format_html(
            '<span style="color: green; font-weight: bold;">{}</span>',
            count
        )
    productos_activos.short_description = 'Activos'


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    """Administración de productos con integración FIFO"""
    
    list_display = (
        'sku',
        'nombre',
        'categoria',
        'precio_venta',
        'stock_actual_display',
        'stock_minimo',
        'estado_stock',
        'valor_inventario_display',
        'necesita_reposicion',
        'activo'
    )
    list_filter = ('activo', 'categoria', 'fecha_creacion')
    search_fields = ('sku', 'codigo_barras', 'nombre', 'descripcion')
    ordering = ('nombre',)
    list_per_page = 50
    
    fieldsets = (
        ('Identificación', {
            'fields': ('sku', 'codigo_barras')
        }),
        ('Información básica', {
            'fields': ('nombre', 'descripcion', 'categoria', 'imagen')
        }),
        ('Precios y stock', {
            'fields': ('precio_venta', 'stock_minimo')
        }),
        ('Inventario FIFO (Solo lectura)', {
            'fields': ('stock_actual_readonly', 'valor_inventario_readonly', 'lotes_disponibles'),
            'description': 'Información calculada desde el sistema FIFO'
        }),
        ('Estado', {
            'fields': ('activo',)
        }),
        ('Información del sistema', {
            'fields': ('fecha_creacion', 'fecha_modificacion'),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = (
        'fecha_creacion', 
        'fecha_modificacion',
        'stock_actual_readonly',
        'valor_inventario_readonly',
        'lotes_disponibles'
    )
    
    # Acciones personalizadas
    actions = [
        'activar_productos', 
        'desactivar_productos',
        'generar_reporte_stock',
        'ver_lotes_producto'
    ]
    
    def stock_actual_display(self, obj):
        """Stock actual con badge de color"""
        from apps.inventario.fifo_logic import obtener_stock_disponible
        stock = obtener_stock_disponible(obj.id)
        
        if stock == 0:
            color = 'red'
            icon = '⭕'
        elif stock < obj.stock_minimo:
            color = 'orange'
            icon = '⚠️'
        else:
            color = 'green'
            icon = '✅'
        
        return format_html(
            '<span style="color: {}; font-weight: bold;">{} {}</span>',
            color, icon, stock
        )
    stock_actual_display.short_description = 'Stock Actual'
    
    def stock_actual_readonly(self, obj):
        """Campo readonly de stock para fieldsets"""
        from apps.inventario.fifo_logic import obtener_stock_disponible
        stock = obtener_stock_disponible(obj.id)
        return f"{stock} unidades"
    stock_actual_readonly.short_description = 'Stock Actual'
    
    def estado_stock(self, obj):
        """Badge visual del estado del stock"""
        from apps.inventario.fifo_logic import obtener_stock_disponible
        stock = obtener_stock_disponible(obj.id)
        
        if stock == 0:
            return format_html('<span style="background-color: #fee; color: red; padding: 3px 8px; border-radius: 3px; font-weight: bold;">AGOTADO</span>')
        elif stock < obj.stock_minimo:
            return format_html('<span style="background-color: #ffe; color: orange; padding: 3px 8px; border-radius: 3px; font-weight: bold;">BAJO</span>')
        else:
            return format_html('<span style="background-color: #efe; color: green; padding: 3px 8px; border-radius: 3px; font-weight: bold;">OK</span>')
    estado_stock.short_description = 'Estado'
    
    def valor_inventario_display(self, obj):
        """Valor del inventario en lista"""
        from apps.inventario.fifo_logic import calcular_valuacion_fifo
        valor = calcular_valuacion_fifo(obj.id)
        """return format_html(
            '<span style="color: green; font-weight: bold;">${:.2f}</span>',
            valor
        )"""
    valor_inventario_display.short_description = 'Valor'
    
    def valor_inventario_readonly(self, obj):
        """Campo readonly de valor para fieldsets"""
        from apps.inventario.fifo_logic import calcular_valuacion_fifo
        valor = calcular_valuacion_fifo(obj.id)
        return f"${valor:.2f}"
    valor_inventario_readonly.short_description = 'Valor Total Inventario'
    
    def lotes_disponibles(self, obj):
        """Muestra información de lotes disponibles"""
        from apps.inventario.models import Lote
        
        lotes = Lote.objects.filter(
            producto=obj,
            cantidad_actual__gt=0,
            activo=True
        ).order_by('fecha_compra')
        
        if not lotes.exists():
            return "Sin lotes disponibles"
        
        html = '<table style="width: 100%; border-collapse: collapse;">'
        html += '<tr style="background-color: #f0f0f0; font-weight: bold;">'
        html += '<th style="padding: 5px; text-align: left;">Lote</th>'
        html += '<th style="padding: 5px; text-align: center;">Cantidad</th>'
        html += '<th style="padding: 5px; text-align: right;">Costo Unit.</th>'
        html += '<th style="padding: 5px; text-align: right;">Valor</th>'
        html += '</tr>'
        
        for lote in lotes[:5]:  # Máximo 5 lotes
            html += '<tr style="border-bottom: 1px solid #ddd;">'
            html += f'<td style="padding: 5px;">{lote.numero_lote}</td>'
            html += f'<td style="padding: 5px; text-align: center;">{lote.cantidad_actual}</td>'
            html += f'<td style="padding: 5px; text-align: right;">${lote.costo_unitario:.2f}</td>'
            html += f'<td style="padding: 5px; text-align: right;">${lote.get_valor_actual():.2f}</td>'
            html += '</tr>'
        
        if lotes.count() > 5:
            html += f'<tr><td colspan="4" style="padding: 5px; text-align: center; font-style: italic;">... y {lotes.count() - 5} lote(s) más</td></tr>'
        
        html += '</table>'
        return format_html(html)
    lotes_disponibles.short_description = 'Lotes Disponibles (FIFO)'
    
    def necesita_reposicion(self, obj):
        """Indica si necesita reposición"""
        from apps.inventario.fifo_logic import obtener_stock_disponible
        stock = obtener_stock_disponible(obj.id)
        necesita = stock < obj.stock_minimo
        
        if necesita:
            return format_html('<span style="color: red; font-weight: bold;">⚠️ SÍ</span>')
        return format_html('<span style="color: green;">✅ NO</span>')
    necesita_reposicion.short_description = 'Reposición'
    
    # === ACCIONES PERSONALIZADAS ===
    
    def activar_productos(self, request, queryset):
        """Activa los productos seleccionados"""
        actualizados = queryset.update(activo=True)
        self.message_user(
            request, 
            f'{actualizados} producto(s) activado(s).', 
            level='success'
        )
    activar_productos.short_description = "✅ Activar productos seleccionados"
    
    def desactivar_productos(self, request, queryset):
        """Desactiva los productos seleccionados"""
        actualizados = queryset.update(activo=False)
        self.message_user(
            request, 
            f'{actualizados} producto(s) desactivado(s).', 
            level='warning'
        )
    desactivar_productos.short_description = "❌ Desactivar productos seleccionados"
    
    def generar_reporte_stock(self, request, queryset):
        """Genera reporte de stock de productos seleccionados"""
        from apps.inventario.fifo_logic import obtener_stock_disponible, calcular_valuacion_fifo
        
        productos_info = []
        for producto in queryset:
            stock = obtener_stock_disponible(producto.id)
            valor = calcular_valuacion_fifo(producto.id)
            productos_info.append({
                'nombre': producto.nombre,
                'stock': stock,
                'valor': valor
            })
        
        # Por ahora solo mostrar mensaje, en el futuro exportar a Excel/PDF
        total_items = sum(p['stock'] for p in productos_info)
        total_valor = sum(p['valor'] for p in productos_info)
        
        self.message_user(
            request,
            f'Reporte generado: {queryset.count()} productos, {total_items} unidades, ${total_valor:.2f} total',
            level='info'
        )
    generar_reporte_stock.short_description = "📊 Generar reporte de stock"
    
    def ver_lotes_producto(self, request, queryset):
        """Redirige a ver lotes de productos seleccionados"""
        if queryset.count() != 1:
            self.message_user(
                request,
                'Selecciona solo UN producto para ver sus lotes',
                level='warning'
            )
            return
        
        producto = queryset.first()
        from django.shortcuts import redirect
        from django.urls import reverse
        
        # Redirigir al admin de lotes filtrado por este producto
        url = reverse('admin:inventario_lote_changelist')
        return redirect(f"{url}?producto__id__exact={producto.id}")
    ver_lotes_producto.short_description = "🔍 Ver lotes del producto"