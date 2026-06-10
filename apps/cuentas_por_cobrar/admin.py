from django.contrib import admin

from .models import CuentaPorCobrar, CuotaCxC, MetodoPlazoCredito, PagoCxC


@admin.register(MetodoPlazoCredito)
class MetodoPlazoCreditoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'tipo', 'dias_vencimiento', 'cantidad_cuotas', 'inicial_minima_porcentaje', 'interes_porcentaje', 'activo')
    list_filter = ('tipo', 'activo', 'sucursal')
    search_fields = ('nombre',)


class CuotaCxCInline(admin.TabularInline):
    model = CuotaCxC
    extra = 0
    readonly_fields = ('numero', 'monto', 'saldo', 'fecha_vencimiento', 'estado', 'fecha_pago')
    can_delete = False


class PagoCxCInline(admin.TabularInline):
    model = PagoCxC
    extra = 0
    readonly_fields = ('metodo', 'monto', 'referencia', 'fecha_pago', 'registrado_por', 'estado', 'aplicaciones')
    can_delete = False


@admin.register(CuentaPorCobrar)
class CuentaPorCobrarAdmin(admin.ModelAdmin):
    list_display = ('venta', 'cliente', 'total', 'saldo_original', 'monto_interes', 'saldo', 'estado', 'fecha_limite')
    list_filter = ('estado', 'metodo_plazo', 'sucursal')
    search_fields = ('venta__numero_venta', 'cliente__nombre', 'cliente__cedula_rnc')
    readonly_fields = ('fecha_creacion', 'fecha_modificacion')
    inlines = (CuotaCxCInline, PagoCxCInline)


@admin.register(CuotaCxC)
class CuotaCxCAdmin(admin.ModelAdmin):
    list_display = ('cuenta', 'numero', 'monto', 'saldo', 'fecha_vencimiento', 'estado')
    list_filter = ('estado', 'fecha_vencimiento')
    search_fields = ('cuenta__venta__numero_venta', 'cuenta__cliente__nombre')


@admin.register(PagoCxC)
class PagoCxCAdmin(admin.ModelAdmin):
    list_display = ('cuenta', 'metodo', 'monto', 'fecha_pago', 'registrado_por', 'estado')
    list_filter = ('metodo', 'estado', 'fecha_pago')
    search_fields = ('cuenta__venta__numero_venta', 'cuenta__cliente__nombre', 'referencia')
