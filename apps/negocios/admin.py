from django.contrib import admin

from .models import Negocio


@admin.register(Negocio)
class NegocioAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'slug', 'rnc', 'activo', 'fecha_creacion')
    list_filter = ('activo',)
    search_fields = ('nombre', 'slug', 'rnc')
    prepopulated_fields = {'slug': ('nombre',)}
    readonly_fields = ('fecha_creacion', 'fecha_modificacion')
