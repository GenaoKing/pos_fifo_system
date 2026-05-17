"""
apps/facturacion_electronica/admin.py

Admin Django para gestión y diagnóstico de e-CFs. Pensado para uso del
SYSADMIN cuando hay que investigar incidencias (rechazos DGII, ECFs
atascados en PENDIENTE, etc.).
"""
from django.contrib import admin
from django.utils.html import format_html

from .models import Emisor, ECF, EventoECF


@admin.register(Emisor)
class EmisorAdmin(admin.ModelAdmin):
    list_display = ('rnc', 'razon_social', 'proveedor_actual', 'activo', 'creado_en')
    list_filter = ('proveedor_actual', 'activo')
    search_fields = ('rnc', 'razon_social', 'nombre_comercial')
    readonly_fields = ('creado_en', 'actualizado_en')

    fieldsets = (
        ('Identidad fiscal', {
            'fields': ('rnc', 'razon_social', 'nombre_comercial', 'direccion'),
        }),
        ('Proveedor de emisión', {
            'fields': ('proveedor_actual', 'config_proveedor'),
            'description': (
                'En config_proveedor guardar referencias a env vars '
                '(ej: {"api_key_env": "MSELLER_API_KEY"}), no los valores '
                'sensibles directamente.'
            ),
        }),
        ('Estado', {
            'fields': ('activo', 'creado_en', 'actualizado_en'),
        }),
    )


class EventoECFInline(admin.TabularInline):
    model = EventoECF
    extra = 0
    can_delete = False
    readonly_fields = ('fecha', 'estado_anterior', 'estado_nuevo', 'mensaje', 'payload')
    ordering = ('-fecha',)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ECF)
class ECFAdmin(admin.ModelAdmin):
    list_display = (
        'encf_display', 'tipo', 'estado_badge', 'emisor', 'venta',
        'proveedor_usado', 'fecha_emision', 'intentos',
    )
    list_filter = ('estado', 'tipo', 'proveedor_usado', 'emisor')
    search_fields = ('encf', 'track_id', 'venta__numero_venta')
    readonly_fields = (
        'fecha_emision', 'creado_en', 'actualizado_en',
        'xml_firmado', 'xml_respuesta', 'intentos',
    )
    raw_id_fields = ('venta',)
    date_hierarchy = 'fecha_emision'
    inlines = [EventoECFInline]

    fieldsets = (
        ('Identificación', {
            'fields': ('emisor', 'tipo', 'encf', 'track_id', 'codigo_seguridad'),
        }),
        ('Vínculos', {
            'fields': ('venta',),
        }),
        ('Estado', {
            'fields': ('estado', 'proveedor_usado', 'intentos'),
        }),
        ('XMLs', {
            'fields': ('xml_firmado', 'xml_respuesta'),
            'classes': ('collapse',),
        }),
        ('Timestamps', {
            'fields': ('fecha_emision', 'creado_en', 'actualizado_en'),
            'classes': ('collapse',),
        }),
    )

    def encf_display(self, obj):
        return obj.encf or format_html('<em>—</em>')
    encf_display.short_description = 'eNCF'

    def estado_badge(self, obj):
        colors = {
            'PENDIENTE': '#6b7280',
            'ENVIADO': '#3b82f6',
            'EN_PROCESO': '#3b82f6',
            'APROBADO': '#10b981',
            'APROBADO_CONDICIONAL': '#f59e0b',
            'RECHAZADO': '#ef4444',
            'ERROR': '#dc2626',
        }
        color = colors.get(obj.estado, '#6b7280')
        return format_html(
            '<span style="background:{}; color:white; padding:2px 8px; '
            'border-radius:4px; font-size:11px; font-weight:600;">{}</span>',
            color, obj.get_estado_display(),
        )
    estado_badge.short_description = 'Estado'


@admin.register(EventoECF)
class EventoECFAdmin(admin.ModelAdmin):
    list_display = ('ecf', 'fecha', 'estado_anterior', 'estado_nuevo', 'mensaje_corto')
    list_filter = ('estado_nuevo',)
    search_fields = ('ecf__encf', 'ecf__track_id', 'mensaje')
    readonly_fields = ('fecha', 'ecf', 'estado_anterior', 'estado_nuevo', 'mensaje', 'payload')
    date_hierarchy = 'fecha'

    def mensaje_corto(self, obj):
        if not obj.mensaje:
            return '—'
        return obj.mensaje[:60] + ('…' if len(obj.mensaje) > 60 else '')
    mensaje_corto.short_description = 'Mensaje'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False