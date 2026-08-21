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
    # `fecha_compra` es auto_now_add, o sea NO editable: tenerla en un fieldset
    # sin declararla readonly hacia que Django levantara FieldError al construir
    # el formulario, y la pantalla de alta/cambio moria antes de renderizar.
    # `manage.py check` no lo detecta porque el form se arma en el request.
    readonly_fields = ('numero_compra', 'fecha_compra', 'fecha_creacion')
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
    
    def has_add_permission(self, request):
        """
        Las compras se registran por la UI, que si emite el outbox.

        Crear una compra desde el Admin generaria lotes y movimientos pero
        NINGUN evento de sync: el stock existiria local y el cloud nunca se
        enteraria. Misma razon por la que el Admin de ventas es de solo lectura.
        """
        return False

    def has_change_permission(self, request, obj=None):
        """Para corregir una compra: `inventario:compra_editar`, que propaga a
        lote, movimiento, auditoria y outbox."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Borrar la compra arrastraria sus detalles y lotes por CASCADE."""
        return False


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
        """Porcentaje consumido del lote.

        Se calcula aca: `Lote` nunca tuvo `get_porcentaje_consumido()`, asi que
        este callback levantaba AttributeError y tumbaba la pantalla de detalle.
        """
        inicial = obj.cantidad_inicial or 0
        if inicial <= 0:
            return 'n/d'
        consumido = (inicial - obj.cantidad_actual) / inicial * 100
        return f'{consumido:.1f}%'
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
    # Un ajuste aplicado es un hecho: inmutable desde el Admin.
    #
    # Antes lote, tipo, cantidad, motivo y usuario eran editables, y como
    # `AjusteInventario.save()` reaplicaba la cantidad en cada guardado,
    # corregir el texto del motivo descontaba el stock otra vez. Ahora el
    # modelo ya no mueve inventario, pero editar el registro igual mentiria
    # sobre un movimiento ya escrito. Para corregir stock: registrar otro
    # ajuste (queda la traza de ambos).
    readonly_fields = (
        'lote', 'tipo', 'cantidad', 'motivo', 'usuario', 'fecha_ajuste',
    )
    
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
    
    def has_add_permission(self, request):
        """Los ajustes se registran por el endpoint, que aplica el service
        (lock del lote, revalidacion y UN movimiento)."""
        return False

    def has_change_permission(self, request, obj=None):
        """Un ajuste aplicado es un hecho. Para corregir: otro ajuste."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Borrarlo dejaria su MovimientoLote sin referencia."""
        return False


