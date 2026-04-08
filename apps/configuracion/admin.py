from django.contrib import admin
from .models import ConfiguracionNegocio


@admin.register(ConfiguracionNegocio)
class ConfiguracionNegocioAdmin(admin.ModelAdmin):
    fieldsets = (
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
                'hora_cierre_automatico', 'dias_limite_anulacion',
                'stock_minimo_default', 'prefijo_numero_venta',
                'formato_codigo_barras', 'permitir_inventario_negativo',
            )
        }),
        ('Impresion', {
            'fields': (
                'nombre_impresora_termica', 'nombre_impresora_zebra',
                'texto_pie_ticket', 'imprimir_logo_ticket',
            )
        }),
    )

    def has_add_permission(self, request):
        return not ConfiguracionNegocio.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False