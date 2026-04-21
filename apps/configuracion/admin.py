"""
apps/configuracion/admin.py
FASE 2: Ya no restringe a un solo registro. Ahora permite una config por sucursal.
"""
from django.contrib import admin
from .models import ConfiguracionNegocio


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
            )
        }),
        ('Metodos de Pago', {
            'fields': ('pago_efectivo', 'pago_transferencia', 'pago_tarjeta')
        }),
        ('Parametros Operativos', {
            'fields': (
                'formato_codigo_barras', 'dias_anulacion',
            )
        }),
    )

    def has_add_permission(self, request):
        # Fase 2: permitir multiples configs (una por sucursal)
        return True

    def has_delete_permission(self, request, obj=None):
        return False