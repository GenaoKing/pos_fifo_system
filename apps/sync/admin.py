"""
apps/sync/admin.py

Admin para monitorear y diagnosticar el sync desde /admin/.
"""
from django.contrib import admin
from django.utils.html import format_html

from .models import (
    EventoSync,
    InventarioMovimientoSync,
    InventarioSucursalSnapshot,
    LogSync,
    VersionMaestro,
)


@admin.register(EventoSync)
class EventoSyncAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'tipo_evento', 'objeto_referencia', 'estado_badge',
        'intentos', 'sucursal', 'created_at', 'confirmed_at',
    )
    list_filter = ('estado', 'tipo_evento', 'sucursal')
    search_fields = ('objeto_referencia', 'hash_payload', 'ultimo_error')
    readonly_fields = (
        'tipo_evento', 'objeto_referencia', 'objeto_id_local',
        'payload', 'hash_payload', 'created_at', 'sent_at', 'confirmed_at',
    )
    date_hierarchy = 'created_at'
    actions = ['reintentar_eventos', 'descartar_eventos']

    fieldsets = (
        ('Evento', {
            'fields': ('tipo_evento', 'sucursal', 'objeto_referencia', 'objeto_id_local'),
        }),
        ('Estado', {
            'fields': ('estado', 'intentos', 'ultimo_error'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'sent_at', 'confirmed_at'),
        }),
        ('Payload (debug)', {
            'fields': ('hash_payload', 'payload'),
            'classes': ('collapse',),
        }),
    )

    def estado_badge(self, obj):
        colors = {
            'PENDIENTE': '#f59e0b',
            'CONFIRMADO': '#10b981',
            'ERROR': '#ef4444',
            'DESCARTADO': '#6b7280',
        }
        color = colors.get(obj.estado, '#6b7280')
        return format_html(
            '<span style="background:{};color:white;padding:3px 8px;border-radius:10px;font-size:11px;">{}</span>',
            color, obj.get_estado_display()
        )
    estado_badge.short_description = 'Estado'

    @admin.action(description='Reintentar eventos seleccionados (vuelve a la cola)')
    def reintentar_eventos(self, request, queryset):
        """
        Delega en `reactivar_eventos`, que ademas reinicia el contador.

        Poner el estado en PENDIENTE no alcanza: el push excluye las filas con
        `intentos >= SYNC_MAX_RETRIES`, asi que un evento DESCARTADO seguia
        invisible para el daemon mientras el Admin informaba que estaba en cola.
        """
        from .models import reactivar_eventos

        count = reactivar_eventos(
            queryset.filter(estado__in=['ERROR', 'DESCARTADO'])
        )
        self.message_user(request, f'{count} eventos puestos en cola de reintento.')

    @admin.action(description='Descartar eventos seleccionados')
    def descartar_eventos(self, request, queryset):
        count = queryset.exclude(estado='CONFIRMADO').update(estado='DESCARTADO')
        self.message_user(request, f'{count} eventos descartados.')


@admin.register(VersionMaestro)
class VersionMaestroAdmin(admin.ModelAdmin):
    list_display = ('tabla', 'ultima_version', 'ultima_sync_exitosa', 'registros_ultima_sync')
    readonly_fields = ('tabla',)


@admin.register(LogSync)
class LogSyncAdmin(admin.ModelAdmin):
    list_display = (
        'inicio', 'tipo', 'resultado_badge', 'duracion_ms',
        'eventos_procesados', 'eventos_exitosos', 'eventos_fallidos',
        'registros_descargados', 'sucursal',
    )
    list_filter = ('tipo', 'resultado', 'sucursal')
    search_fields = ('mensaje',)
    readonly_fields = (
        'tipo', 'resultado', 'sucursal', 'inicio', 'fin', 'duracion_ms',
        'eventos_procesados', 'eventos_exitosos', 'eventos_fallidos',
        'registros_descargados', 'mensaje',
    )
    date_hierarchy = 'inicio'

    def resultado_badge(self, obj):
        colors = {
            'EXITOSO': '#10b981',
            'PARCIAL': '#f59e0b',
            'FALLO': '#ef4444',
        }
        color = colors.get(obj.resultado, '#6b7280')
        return format_html(
            '<span style="background:{};color:white;padding:3px 8px;border-radius:10px;font-size:11px;">{}</span>',
            color, obj.get_resultado_display()
        )
    resultado_badge.short_description = 'Resultado'

    def has_add_permission(self, request):
        return False


@admin.register(InventarioMovimientoSync)
class InventarioMovimientoSyncAdmin(admin.ModelAdmin):
    list_display = (
        'fecha_movimiento', 'sucursal', 'producto_sku', 'tipo', 'cantidad',
        'referencia_tipo', 'referencia_id',
    )
    list_filter = ('sucursal', 'tipo', 'fecha_movimiento')
    search_fields = ('producto_sku', 'producto_nombre', 'lote_numero', 'usuario_username', 'notas')
    readonly_fields = [field.name for field in InventarioMovimientoSync._meta.fields]
    date_hierarchy = 'fecha_movimiento'

    def has_add_permission(self, request):
        return False


@admin.register(InventarioSucursalSnapshot)
class InventarioSucursalSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        'sucursal', 'producto_sku', 'producto_nombre', 'stock_actual',
        'stock_minimo', 'bajo_stock', 'valor_fifo', 'timestamp',
    )
    list_filter = ('sucursal', 'bajo_stock')
    search_fields = ('producto_sku', 'producto_nombre')
    readonly_fields = [field.name for field in InventarioSucursalSnapshot._meta.fields]
    date_hierarchy = 'timestamp'

    def has_add_permission(self, request):
        return False
