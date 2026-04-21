"""
apps/sucursales/admin.py
"""
from django.contrib import admin
from .models import Sucursal


@admin.register(Sucursal)
class SucursalAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'nombre', 'telefono', 'activa', 'fecha_creacion')
    list_filter = ('activa',)
    search_fields = ('codigo', 'nombre')
    readonly_fields = ('api_key', 'fecha_creacion', 'fecha_modificacion')

    fieldsets = (
        ('Identificacion', {
            'fields': ('codigo', 'nombre')
        }),
        ('Contacto', {
            'fields': ('direccion', 'telefono'),
        }),
        ('Estado', {
            'fields': ('activa',),
        }),
        ('Sistema', {
            'fields': ('api_key', 'fecha_creacion', 'fecha_modificacion'),
            'classes': ('collapse',),
        }),
    )