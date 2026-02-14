from django.contrib import admin
from django.utils.html import format_html
from .models import Venta, DetalleVenta, Pago


class DetalleVentaInline(admin.TabularInline):
    """Inline para detalles de venta"""
    model = DetalleVenta
    extra = 0
    fields = (
        'producto', 
        'cantidad', 
        'precio_unitario', 
        'descuento_monto',
        'total_linea',
        'costo_fifo',
        'margen'
    )
    readonly_fields = ('total_linea', 'costo_fifo', 'margen')
    
    def margen(self, obj):
        """Muestra el margen de la línea"""
        if obj.pk:
            return f"${obj.get_margen_bruto():.2f} ({obj.get_margen_porcentaje():.1f}%)"
        return "-"
    margen.short_description = 'Margen'


class PagoInline(admin.TabularInline):
    """Inline para pagos"""
    model = Pago
    extra = 0
    fields = ('metodo', 'monto', 'referencia', 'fecha_pago')
    readonly_fields = ('fecha_pago',)


@admin.register(Venta)
class VentaAdmin(admin.ModelAdmin):
    """Admin para Ventas"""
    list_display = (
        'numero_venta',
        'fecha_venta',
        'usuario',
        'total_badge',
        'estado_badge',
        'puede_anular'
    )
    list_filter = ('estado', 'fecha_venta', 'usuario')
    search_fields = ('numero_venta',)
    readonly_fields = (
        'numero_venta',
        'fecha_venta',
        'subtotal',
        'descuento_total',
        'total',
        'total_pagado',
        'fecha_anulacion',
        'anulada_por'
    )
    inlines = [DetalleVentaInline, PagoInline]
    
    fieldsets = (
        ('Información General', {
            'fields': ('numero_venta', 'fecha_venta', 'usuario', 'estado')
        }),
        ('Totales', {
            'fields': ('subtotal', 'descuento_total', 'total', 'total_pagado')
        }),
        ('Notas', {
            'fields': ('notas',),
            'classes': ('collapse',)
        }),
        ('Anulación', {
            'fields': ('fecha_anulacion', 'anulada_por', 'motivo_anulacion'),
            'classes': ('collapse',)
        }),
    )
    
    def total_badge(self, obj):
        """Badge de total con color"""
        return format_html(
            '<span style="font-weight: bold; color: green;">${:.2f}</span>',
            obj.total
        )
    total_badge.short_description = 'Total'
    
    def estado_badge(self, obj):
        """Badge de estado"""
        if obj.estado == 'COMPLETADA':
            return format_html('<span style="color: green;">✅ Completada</span>')
        else:
            return format_html('<span style="color: red;">❌ Anulada</span>')
    estado_badge.short_description = 'Estado'
    
    def puede_anular(self, obj):
        """Indica si puede anularse"""
        if obj.puede_anularse():
            return format_html('<span style="color: green;">✅ Sí</span>')
        else:
            return format_html('<span style="color: red;">❌ No</span>')
    puede_anular.short_description = '¿Anulable?'
    
    def total_pagado(self, obj):
        """Total pagado (suma de pagos)"""
        total = sum(p.monto for p in obj.pagos.all())
        return f"${total:.2f}"
    total_pagado.short_description = 'Total Pagado'
    
    def has_delete_permission(self, request, obj=None):
        """No permitir eliminar ventas"""
        return False


@admin.register(DetalleVenta)
class DetalleVentaAdmin(admin.ModelAdmin):
    """Admin para Detalles de Venta"""
    list_display = (
        'venta',
        'producto',
        'cantidad',
        'precio_unitario',
        'descuento_monto',
        'total_linea',
        'margen_display'
    )
    list_filter = ('venta__fecha_venta', 'producto')
    search_fields = ('venta__numero_venta', 'producto__nombre')
    readonly_fields = (
        'venta',
        'producto',
        'cantidad',
        'precio_unitario',
        'subtotal',
        'descuento_monto',
        'descuento_porcentaje',
        'total_linea',
        'costo_fifo',
        'margen_bruto',
        'margen_porcentaje'
    )
    
    fieldsets = (
        ('Venta', {
            'fields': ('venta', 'producto', 'cantidad')
        }),
        ('Precios', {
            'fields': (
                'precio_unitario',
                'subtotal',
                'descuento_monto',
                'descuento_porcentaje',
                'total_linea'
            )
        }),
        ('Costos y Margen', {
            'fields': ('costo_fifo', 'margen_bruto', 'margen_porcentaje')
        }),
    )
    
    def margen_display(self, obj):
        """Muestra margen con colores"""
        margen = obj.get_margen_porcentaje()
        if margen >= 30:
            color = 'green'
        elif margen >= 15:
            color = 'orange'
        else:
            color = 'red'
        
        return format_html(
            '<span style="color: {}; font-weight: bold;">{:.1f}%</span>',
            color,
            margen
        )
    margen_display.short_description = 'Margen %'
    
    def margen_bruto(self, obj):
        """Margen bruto en dinero"""
        return f"${obj.get_margen_bruto():.2f}"
    margen_bruto.short_description = 'Margen $'
    
    def margen_porcentaje(self, obj):
        """Margen en porcentaje"""
        return f"{obj.get_margen_porcentaje():.2f}%"
    margen_porcentaje.short_description = 'Margen %'
    
    def has_add_permission(self, request):
        """No crear detalles manualmente"""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """No eliminar detalles"""
        return False


@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    """Admin para Pagos"""
    list_display = (
        'fecha_pago',
        'venta',
        'metodo',
        'monto',
        'referencia'
    )
    list_filter = ('metodo', 'fecha_pago')
    search_fields = ('venta__numero_venta', 'referencia')
    readonly_fields = ('venta', 'metodo', 'monto', 'referencia', 'fecha_pago')
    
    def has_add_permission(self, request):
        """No crear pagos manualmente"""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """No eliminar pagos"""
        return False