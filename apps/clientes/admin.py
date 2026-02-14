from django.contrib import admin
from .models import Cliente


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'tipo', 'cedula_rnc', 'telefono', 'activo']
    list_filter = ['tipo', 'activo']
    search_fields = ['nombre', 'cedula_rnc', 'telefono']
    ordering = ['nombre']