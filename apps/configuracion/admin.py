"""
apps/configuracion/admin.py
FASE 2: Ya no restringe a un solo registro. Ahora permite una config por sucursal.
"""
from django.contrib import admin
from .models import AccesoRapidoPOS, ConfiguracionNegocio


@admin.register(ConfiguracionNegocio)
class ConfiguracionNegocioAdmin(admin.ModelAdmin):
    list_display = ('nombre_negocio', 'sucursal', 'fecha_modificacion')
    list_filter = ('sucursal',)

    fieldsets = (
        ('Sucursal', {
            'fields': ('sucursal',),
            'description': 'Sucursal a la que pertenece esta configuracion.'
        }),
        ('Identidad del Negocio', {
            'fields': ('nombre_negocio', 'rnc', 'direccion', 'telefono', 'email_negocio', 'logo')
        }),
        ('Modulos', {
            'fields': (
                'modulo_etiquetas_zebra', 'modulo_financiacion_coop',
                'modulo_cotizaciones', 'modulo_impresion_termica',
                'modulo_barcode_scanner', 'modulo_reportes_ondemand',
                'modulo_ecf', 'modulo_dashboard',
                'permitir_inventario_negativo',
            )
        }),
        ('Metodos de Pago', {
            'fields': ('pago_efectivo', 'pago_transferencia', 'pago_tarjeta')
        }),
        ('Parametros Operativos', {
            'fields': (
                'formato_codigo_barras', 'dias_anulacion',
                'cantidad_copias_ticket',
            )
        }),
    )

    def has_add_permission(self, request):
        # Fase 2: permitir multiples configs (una por sucursal)
        return True

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AccesoRapidoPOS)
class AccesoRapidoPOSAdmin(admin.ModelAdmin):
    list_display = (
        'orden',
        'etiqueta_visible',
        'tipo',
        'producto',
        'categoria',
        'color',
        'activo',
        'fecha_modificacion',
    )
    list_filter = ('tipo', 'activo', 'color')
    list_display_links = ('etiqueta_visible',)
    search_fields = (
        'etiqueta',
        'producto__nombre',
        'producto__sku',
        'producto__codigo_barras',
        'categoria__nombre',
    )
    autocomplete_fields = ('producto', 'categoria')
    list_editable = ('orden', 'activo')
    ordering = ('orden', 'id')
    fieldsets = (
        ('Boton', {
            'fields': ('etiqueta', 'tipo', 'color', 'orden', 'activo')
        }),
        ('Destino', {
            'fields': ('producto', 'categoria'),
            'description': 'Use producto para agregar al carrito; use categoria para filtrar resultados en el POS.',
        }),
    )
