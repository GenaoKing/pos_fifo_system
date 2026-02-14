from django.contrib import admin
from django.utils.html import format_html
from .models import Compra, DetalleCompra, Lote, MovimientoLote, AjusteInventario


class DetalleCompraInline(admin.TabularInline):
    """Inline para detalles de compra"""
    model = DetalleCompra
    extra = 1
    fields = ('producto', 'cantidad', 'costo_unitario', 'subtotal')
    readonly_fields = ('subtotal',)


@admin.register(Compra)
class CompraAdmin(admin.ModelAdmin):
    """Admin para Compras"""
    list_display = ('numero_compra', 'proveedor', 'fecha_compra', 'total', 'usuario', 'fecha_creacion')
    list_filter = ('fecha_compra', 'proveedor')
    search_fields = ('numero_compra', 'proveedor')
    readonly_fields = ('numero_compra', 'fecha_creacion')
    inlines = [DetalleCompraInline]
    
    fieldsets = (
        ('Información General', {
            'fields': ('numero_compra', 'proveedor', 'fecha_compra')
        }),
        ('Totales', {
            'fields': ('total',)
        }),
        ('Notas', {
            'fields': ('notas',),
            'classes': ('collapse',)
        }),
        ('Auditoría', {
            'fields': ('usuario', 'fecha_creacion'),
            'classes': ('collapse',)
        }),
    )
    
    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.usuario = request.user
        super().save_model(request, obj, form, change)


@admin.register(Lote)
class LoteAdmin(admin.ModelAdmin):
    """Admin para Lotes FIFO"""
    list_display = (
        'numero_lote', 
        'producto', 
        'fecha_compra', 
        'cantidad_disponible',
        'costo_unitario', 
        'valor_actual',
        'estado_badge',
        'activo'
    )
    list_filter = ('activo', 'fecha_compra', 'producto__categoria')
    search_fields = ('numero_lote', 'producto__nombre', 'producto__sku')
    readonly_fields = (
        'numero_lote', 
        'fecha_compra', 
        'cantidad_inicial', 
        'fecha_creacion',
        'valor_actual',
        'porcentaje_consumido'
    )
    
    fieldsets = (
        ('Identificación', {
            'fields': ('numero_lote', 'producto')
        }),
        ('Cantidades', {
            'fields': ('cantidad_inicial', 'cantidad_actual', 'porcentaje_consumido')
        }),
        ('Costo y Valuación', {
            'fields': ('costo_unitario', 'valor_actual')
        }),
        ('Fechas', {
            'fields': ('fecha_compra', 'fecha_creacion')
        }),
        ('Control', {
            'fields': ('activo',)
        }),
    )
    
    def cantidad_disponible(self, obj):
        """Muestra cantidad con formato"""
        return f"{obj.cantidad_actual}/{obj.cantidad_inicial}"
    cantidad_disponible.short_description = 'Disponible'
    
    def valor_actual(self, obj):
        """Valor del lote"""
        return f"${obj.get_valor_actual():.2f}"
    valor_actual.short_description = 'Valor'
    
    def estado_badge(self, obj):
        """Badge de estado del lote"""
        if obj.esta_agotado():
            return format_html('<span style="color: red;">⭕ Agotado</span>')
        elif obj.cantidad_actual < obj.cantidad_inicial * 0.3:
            return format_html('<span style="color: orange;">⚠️ Bajo</span>')
        else:
            return format_html('<span style="color: green;">✅ OK</span>')
    estado_badge.short_description = 'Estado'
    
    def porcentaje_consumido(self, obj):
        """Porcentaje consumido"""
        return f"{obj.get_porcentaje_consumido():.1f}%"
    porcentaje_consumido.short_description = '% Consumido'


@admin.register(MovimientoLote)
class MovimientoLoteAdmin(admin.ModelAdmin):
    """Admin para Movimientos de Lote"""
    list_display = (
        'fecha_creacion',
        'lote',
        'tipo',
        'cantidad_badge',
        'cantidad_anterior',
        'cantidad_nueva',
        'usuario'
    )
    list_filter = ('tipo', 'fecha_creacion', 'lote__producto')
    search_fields = ('lote__numero_lote', 'notas')
    readonly_fields = (
        'lote', 
        'tipo', 
        'cantidad', 
        'cantidad_anterior', 
        'cantidad_nueva',
        'referencia_tipo',
        'referencia_id',
        'usuario',
        'notas',
        'fecha_creacion'
    )
    
    fieldsets = (
        ('Movimiento', {
            'fields': ('lote', 'tipo', 'cantidad', 'cantidad_anterior', 'cantidad_nueva')
        }),
        ('Referencia', {
            'fields': ('referencia_tipo', 'referencia_id')
        }),
        ('Detalles', {
            'fields': ('usuario', 'fecha_creacion', 'notas')
        }),
    )
    
    def has_add_permission(self, request):
        """No permitir crear manualmente"""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """No permitir eliminar (auditoría)"""
        return False
    
    def cantidad_badge(self, obj):
        """Badge con color según tipo de movimiento"""
        if obj.cantidad > 0:
            return format_html(
                '<span style="color: green; font-weight: bold;">+{}</span>',
                obj.cantidad
            )
        else:
            return format_html(
                '<span style="color: red; font-weight: bold;">{}</span>',
                obj.cantidad
            )
    cantidad_badge.short_description = 'Cantidad'


@admin.register(AjusteInventario)
class AjusteInventarioAdmin(admin.ModelAdmin):
    """Admin para Ajustes de Inventario"""
    list_display = (
        'fecha_ajuste',
        'lote',
        'tipo',
        'cantidad',
        'usuario'
    )
    list_filter = ('tipo', 'fecha_ajuste')
    search_fields = ('lote__numero_lote', 'motivo')
    readonly_fields = ('fecha_ajuste',)
    
    fieldsets = (
        ('Ajuste', {
            'fields': ('lote', 'tipo', 'cantidad')
        }),
        ('Justificación', {
            'fields': ('motivo',)
        }),
        ('Auditoría', {
            'fields': ('usuario', 'fecha_ajuste'),
            'classes': ('collapse',)
        }),
    )
    
    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.usuario = request.user
        super().save_model(request, obj, form, change)


