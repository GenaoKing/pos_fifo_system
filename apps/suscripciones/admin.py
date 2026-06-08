from django.contrib import admin

from .models import (
    Modulo,
    NegocioModulo,
    Plan,
    SucursalModuloOverride,
    SuscripcionNegocio,
)


@admin.register(Modulo)
class ModuloAdmin(admin.ModelAdmin):
    list_display = ('key', 'nombre', 'core')
    list_filter = ('core',)
    search_fields = ('key', 'nombre')


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'slug', 'activo')
    list_filter = ('activo',)
    search_fields = ('nombre', 'slug')
    prepopulated_fields = {'slug': ('nombre',)}
    filter_horizontal = ('modulos',)


@admin.register(SuscripcionNegocio)
class SuscripcionNegocioAdmin(admin.ModelAdmin):
    list_display = ('negocio', 'plan', 'activa')
    list_filter = ('activa', 'plan')
    search_fields = ('negocio__nombre',)


@admin.register(NegocioModulo)
class NegocioModuloAdmin(admin.ModelAdmin):
    list_display = ('negocio', 'modulo', 'incluido')
    list_filter = ('incluido', 'modulo')
    search_fields = ('negocio__nombre', 'modulo__key')


@admin.register(SucursalModuloOverride)
class SucursalModuloOverrideAdmin(admin.ModelAdmin):
    list_display = ('sucursal', 'modulo', 'activo')
    list_filter = ('modulo',)
    search_fields = ('sucursal__codigo', 'modulo__key')
