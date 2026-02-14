from django.contrib import admin
from .models import Cotizacion, DetalleCotizacion


class DetalleCotizacionInline(admin.TabularInline):
    model = DetalleCotizacion
    extra = 0
    readonly_fields = ['subtotal', 'total_linea', 'descuento_porcentaje']


@admin.register(Cotizacion)
class CotizacionAdmin(admin.ModelAdmin):
    list_display = ['numero_cotizacion', 'cliente', 'total', 'estado', 'fecha_creacion']
    list_filter = ['estado']
    search_fields = ['numero_cotizacion', 'cliente__nombre']
    inlines = [DetalleCotizacionInline]