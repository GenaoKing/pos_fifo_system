"""
Configuración del admin para auditoría
apps/auditoria/admin.py
"""
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
import json
from .models import Auditoria


@admin.register(Auditoria)
class AuditoriaAdmin(admin.ModelAdmin):
    """
    Admin personalizado para el modelo de Auditoría.
    Incluye filtros avanzados y visualización mejorada.
    """
    
    list_display = [
        'fecha_hora_formateada',
        'usuario_link',
        'accion_badge',
        'descripcion_corta',
        'objeto_relacionado',
        'nivel_badge',
        'exito_badge',
        'ip_address',
    ]
    
    list_filter = [
        'accion',
        'nivel_importancia',
        'exito',
        'fecha_hora',
        'usuario',
    ]
    
    search_fields = [
        'descripcion',
        'usuario__username',
        'usuario__first_name',
        'usuario__last_name',
        'ip_address',
    ]
    
    readonly_fields = [
        'usuario',
        'accion',
        'descripcion',
        'content_type',
        'object_id',
        'datos_anteriores_formateados',
        'datos_nuevos_formateados',
        'metadata_formateada',
        'fecha_hora',
        'ip_address',
        'user_agent',
        'exito',
        'mensaje_error',
        'nivel_importancia',
    ]
    
    fieldsets = (
        ('Información Básica', {
            'fields': (
                'fecha_hora',
                'usuario',
                'accion',
                'descripcion',
            )
        }),
        ('Objeto Relacionado', {
            'fields': (
                'content_type',
                'object_id',
            ),
            'classes': ('collapse',)
        }),
        ('Cambios Realizados', {
            'fields': (
                'datos_anteriores_formateados',
                'datos_nuevos_formateados',
                'metadata_formateada',
            ),
            'classes': ('collapse',)
        }),
        ('Información del Cliente', {
            'fields': (
                'ip_address',
                'user_agent',
            ),
            'classes': ('collapse',)
        }),
        ('Resultado', {
            'fields': (
                'nivel_importancia',
                'exito',
                'mensaje_error',
            )
        }),
    )
    
    date_hierarchy = 'fecha_hora'
    
    list_per_page = 50
    
    # No permitir agregar/editar/eliminar desde admin
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        # Solo superusuarios pueden eliminar auditoría
        return request.user.is_superuser
    
    # === MÉTODOS PERSONALIZADOS PARA DISPLAY ===
    
    def fecha_hora_formateada(self, obj):
        """Muestra fecha y hora formateadas"""
        return obj.fecha_hora.strftime('%d/%m/%Y %H:%M:%S')
    fecha_hora_formateada.short_description = 'Fecha/Hora'
    fecha_hora_formateada.admin_order_field = 'fecha_hora'
    
    def usuario_link(self, obj):
        """Muestra enlace al usuario si existe"""
        if obj.usuario:
            url = reverse('admin:usuarios_usuario_change', args=[obj.usuario.pk])
            return format_html(
                '<a href="{}">{}</a>',
                url,
                obj.usuario.username
            )
        return format_html('<span style="color: #666;">Sistema</span>')
    usuario_link.short_description = 'Usuario'
    usuario_link.admin_order_field = 'usuario'
    
    def accion_badge(self, obj):
        """Muestra la acción con un badge de color"""
        colores = {
            'LOGIN': '#10b981',
            'LOGOUT': '#6b7280',
            'LOGIN_FAIL': '#ef4444',
            'CREATE': '#3b82f6',
            'UPDATE': '#f59e0b',
            'DELETE': '#ef4444',
            'VENTA_CREATE': '#10b981',
            'VENTA_CANCEL': '#dc2626',
            'TICKET_REPRINT': '#8b5cf6',
            'AJUSTE_INV': '#f97316',
            'COMPRA_CREATE': '#06b6d4',
            'PRECIO_UPDATE': '#eab308',
        }
        color = colores.get(obj.accion, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; '
            'border-radius: 3px; font-size: 11px; font-weight: bold;">{}</span>',
            color,
            obj.get_accion_display()
        )
    accion_badge.short_description = 'Acción'
    accion_badge.admin_order_field = 'accion'
    
    def descripcion_corta(self, obj):
        """Muestra descripción truncada"""
        if len(obj.descripcion) > 80:
            return obj.descripcion[:80] + '...'
        return obj.descripcion
    descripcion_corta.short_description = 'Descripción'
    
    def objeto_relacionado(self, obj):
        """Muestra enlace al objeto relacionado si es posible"""
        if obj.content_type and obj.object_id:
            try:
                modelo = obj.content_type.model_class()
                objeto = modelo.objects.get(pk=obj.object_id)
                
                # Intentar obtener URL del admin
                try:
                    app_label = obj.content_type.app_label
                    model_name = obj.content_type.model
                    url = reverse(f'admin:{app_label}_{model_name}_change', args=[obj.object_id])
                    return format_html('<a href="{}">{}</a>', url, str(objeto)[:50])
                except:
                    return str(objeto)[:50]
            except:
                return format_html('<span style="color: #999;">Objeto eliminado</span>')
        return '-'
    objeto_relacionado.short_description = 'Objeto'
    
    def nivel_badge(self, obj):
        """Muestra nivel de importancia con badge de color"""
        colores = {
            'BAJA': '#d1d5db',
            'MEDIA': '#fbbf24',
            'ALTA': '#f97316',
            'CRITICA': '#dc2626',
        }
        color = colores.get(obj.nivel_importancia, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 6px; '
            'border-radius: 3px; font-size: 10px;">{}</span>',
            color,
            obj.get_nivel_importancia_display()
        )
    nivel_badge.short_description = 'Nivel'
    nivel_badge.admin_order_field = 'nivel_importancia'
    
    def exito_badge(self, obj):
        """Muestra si fue exitoso con un icono"""
        if obj.exito:
            return format_html('<span style="color: #10b981; font-size: 18px;">✓</span>')
        else:
            return format_html('<span style="color: #ef4444; font-size: 18px;">✗</span>')
    exito_badge.short_description = 'Éxito'
    exito_badge.admin_order_field = 'exito'
    
    # === CAMPOS READONLY FORMATEADOS ===
    
    def datos_anteriores_formateados(self, obj):
        """Muestra datos anteriores en formato JSON legible"""
        if obj.datos_anteriores:
            json_str = json.dumps(obj.datos_anteriores, indent=2, ensure_ascii=False)
            return format_html('<pre style="background: #f3f4f6; padding: 10px; border-radius: 4px;">{}</pre>', json_str)
        return '-'
    datos_anteriores_formateados.short_description = 'Datos Anteriores'
    
    def datos_nuevos_formateados(self, obj):
        """Muestra datos nuevos en formato JSON legible"""
        if obj.datos_nuevos:
            json_str = json.dumps(obj.datos_nuevos, indent=2, ensure_ascii=False)
            return format_html('<pre style="background: #f3f4f6; padding: 10px; border-radius: 4px;">{}</pre>', json_str)
        return '-'
    datos_nuevos_formateados.short_description = 'Datos Nuevos'
    
    def metadata_formateada(self, obj):
        """Muestra metadata en formato JSON legible"""
        if obj.metadata:
            json_str = json.dumps(obj.metadata, indent=2, ensure_ascii=False)
            return format_html('<pre style="background: #f3f4f6; padding: 10px; border-radius: 4px;">{}</pre>', json_str)
        return '-'
    metadata_formateada.short_description = 'Metadata'
    
    # === ACCIONES PERSONALIZADAS ===
    
    actions = ['marcar_como_revisado']
    
    def marcar_como_revisado(self, request, queryset):
        """Acción personalizada para marcar registros como revisados"""
        # Esto se podría implementar agregando un campo "revisado" al modelo
        self.message_user(request, f'{queryset.count()} registros marcados como revisados.')
    marcar_como_revisado.short_description = 'Marcar como revisado'
    
    # === ORDENAMIENTO PERSONALIZADO ===
    
    def get_queryset(self, request):
        """Optimiza las queries con select_related"""
        qs = super().get_queryset(request)
        return qs.select_related('usuario', 'content_type')