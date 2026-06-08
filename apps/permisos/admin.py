from django.contrib import admin

from .models import AsignacionRol, Permiso, Rol


@admin.register(Permiso)
class PermisoAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'nombre', 'modulo')
    list_filter = ('modulo',)
    search_fields = ('codigo', 'nombre')
    ordering = ('modulo', 'codigo')


@admin.register(Rol)
class RolAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'negocio', 'slug', 'es_sistema', 'activo')
    list_filter = ('negocio', 'es_sistema', 'activo')
    search_fields = ('nombre', 'slug')
    filter_horizontal = ('permisos',)
    readonly_fields = ('fecha_creacion', 'fecha_modificacion')


@admin.register(AsignacionRol)
class AsignacionRolAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'rol', 'sucursal', 'activo')
    list_filter = ('activo', 'rol__negocio')
    search_fields = ('usuario__username', 'rol__nombre')
    autocomplete_fields = ()
    readonly_fields = ('fecha_creacion',)
