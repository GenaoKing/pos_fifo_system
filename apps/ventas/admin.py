"""
apps/ventas/admin.py

Admin de ventas en modo SOLO LECTURA.

Una venta cerrada es un hecho contable: su cabecera, sus líneas y sus pagos
tienen efectos ya propagados (consumo FIFO, cuenta por cobrar, e-CF emitido,
eventos de sync, auditoría). Editarlos campo por campo desde el admin cambiaba
una parte del hecho sin recomponer el resto — por ejemplo, poner `estado` en
ANULADA no devolvía stock, no revertía la CxC, no auditaba ni encolaba la nota
de crédito.

Por eso acá no se crea, no se edita y no se borra nada. La única mutación
posible es la anulación, y va por la acción `anular_ventas`, que llama al mismo
`anular_venta_service` que usa el POS y por lo tanto mantiene las mismas
garantías.
"""
from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.utils.html import format_html

from .models import Venta, DetalleVenta, Pago
from .services import ErrorVentaBase, anular_venta_service


class SoloLecturaInlineMixin:
    """Inline que muestra datos históricos y no permite tocarlos."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class DetalleVentaInline(SoloLecturaInlineMixin, admin.TabularInline):
    """Inline para detalles de venta (solo lectura)"""
    model = DetalleVenta
    extra = 0
    can_delete = False
    fields = (
        'producto',
        'cantidad',
        'precio_unitario',
        'descuento_monto',
        'total_linea',
        'costo_fifo',
        'margen'
    )
    readonly_fields = fields

    def margen(self, obj):
        """Muestra el margen de la línea"""
        if obj.pk:
            return f"${obj.get_margen_bruto():.2f} ({obj.get_margen_porcentaje():.1f}%)"
        return "-"
    margen.short_description = 'Margen'


class PagoInline(SoloLecturaInlineMixin, admin.TabularInline):
    """Inline para pagos (solo lectura)"""
    model = Pago
    extra = 0
    can_delete = False
    fields = ('metodo', 'monto', 'referencia', 'fecha_pago')
    readonly_fields = fields


@admin.register(Venta)
class VentaAdmin(admin.ModelAdmin):
    """Admin para Ventas — consulta y anulación por service."""
    list_display = (
        'numero_venta',
        'fecha_venta',
        'usuario',
        'sucursal',
        'total_badge',
        'estado_badge',
        'puede_anular'
    )
    list_filter = ('estado', 'fecha_venta', 'usuario', 'sucursal')
    search_fields = ('numero_venta',)
    actions = ['anular_ventas']

    # Todo el modelo es histórico: nada editable a mano.
    readonly_fields = (
        'numero_venta',
        'fecha_venta',
        'usuario',
        'sucursal',
        'cliente',
        'condicion_pago',
        'estado',
        'subtotal',
        'descuento_total',
        'descuento_autorizado_por',
        'descuento_autorizacion_motivo',
        'total',
        'total_pagado',
        'notas',
        'fecha_anulacion',
        'anulada_por',
        'motivo_anulacion',
    )
    inlines = [DetalleVentaInline, PagoInline]

    fieldsets = (
        ('Información General', {
            'fields': (
                'numero_venta', 'fecha_venta', 'usuario', 'sucursal',
                'cliente', 'condicion_pago', 'estado',
            )
        }),
        ('Totales', {
            'fields': ('subtotal', 'descuento_total', 'total', 'total_pagado')
        }),
        ('Autorizacion del descuento', {
            'fields': ('descuento_autorizado_por', 'descuento_autorizacion_motivo'),
            'classes': ('collapse',)
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
        # El número se formatea ANTES: `format_html` pasa cada argumento por
        # `conditional_escape`, que devuelve SafeString, y un spec numérico
        # como `{:.2f}` sobre un str levanta ValueError al renderizar la lista.
        return format_html(
            '<span style="font-weight: bold; color: green;">${}</span>',
            f'{obj.total:.2f}',
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

    def has_add_permission(self, request):
        """Las ventas se crean desde el POS, nunca a mano."""
        return False

    def has_delete_permission(self, request, obj=None):
        """No permitir eliminar ventas"""
        return False

    @admin.action(description='Anular ventas seleccionadas (con motivo)')
    def anular_ventas(self, request, queryset):
        """
        Anulación desde el admin delegada al service.

        Pide el motivo en una página intermedia (el service exige un motivo de
        al menos 10 caracteres) y luego procesa venta por venta, reportando el
        resultado de cada una. Cada llamada corre su propia transacción: un
        fallo en la tercera venta no deshace las dos primeras ni las deja a
        medio anular.
        """
        ventas = list(queryset.order_by('numero_venta'))

        if 'aplicar' not in request.POST:
            return render(
                request,
                'admin/ventas/anular_confirmacion.html',
                {
                    **self.admin_site.each_context(request),
                    'title': 'Anular ventas',
                    'ventas': ventas,
                    'action_checkbox_name': admin.helpers.ACTION_CHECKBOX_NAME,
                    'opts': self.model._meta,
                },
            )

        motivo = (request.POST.get('motivo') or '').strip()
        anuladas = 0

        for venta in ventas:
            try:
                anular_venta_service(
                    usuario=request.user,
                    venta_id=venta.id,
                    motivo=motivo,
                    ip_address=None,
                )
            except ErrorVentaBase as exc:
                self.message_user(
                    request,
                    f'{venta.numero_venta}: {exc}',
                    level=messages.ERROR,
                )
            else:
                anuladas += 1

        if anuladas:
            self.message_user(
                request,
                f'{anuladas} venta(s) anuladas correctamente.',
                level=messages.SUCCESS,
            )

        return redirect(request.get_full_path())


@admin.register(DetalleVenta)
class DetalleVentaAdmin(admin.ModelAdmin):
    """Admin para Detalles de Venta (solo lectura)"""
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

        # Ver nota en VentaAdmin.total_badge: el número va preformateado.
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}%</span>',
            color,
            f'{margen:.1f}',
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

    def has_change_permission(self, request, obj=None):
        """Una línea de venta cerrada no se edita: se anula la venta entera."""
        return False

    def has_delete_permission(self, request, obj=None):
        """No eliminar detalles"""
        return False


@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    """Admin para Pagos (solo lectura)"""
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

    def has_change_permission(self, request, obj=None):
        """Un cobro registrado no se edita: descuadra el cierre de caja."""
        return False

    def has_delete_permission(self, request, obj=None):
        """No eliminar pagos"""
        return False
