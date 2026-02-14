from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import Usuario


@admin.register(Usuario)
class UsuarioAdmin(BaseUserAdmin):
    """Administración de usuarios en Django Admin"""
    
    # Campos a mostrar en la lista
    list_display = ('username', 'email', 'rol', 'activo', 'fecha_creacion')
    list_filter = ('rol', 'activo', 'is_staff', 'fecha_creacion')
    search_fields = ('username', 'email', 'first_name', 'last_name')
    ordering = ('-fecha_creacion',)
    
    # Campos en el formulario de edición
    fieldsets = (
        ('Información de acceso', {
            'fields': ('username', 'password')
        }),
        ('Información personal', {
            'fields': ('first_name', 'last_name', 'email')
        }),
        ('Permisos', {
            'fields': ('rol', 'activo', 'is_staff', 'is_superuser', 'groups', 'user_permissions')
        }),
        ('Fechas importantes', {
            'fields': ('ultimo_acceso', 'fecha_creacion', 'fecha_modificacion'),
            'classes': ('collapse',)
        }),
    )
    
    # Campos de solo lectura
    readonly_fields = ('fecha_creacion', 'fecha_modificacion', 'ultimo_acceso')
    
    # Campos al crear nuevo usuario
    add_fieldsets = (
        ('Crear nuevo usuario', {
            'classes': ('wide',),
            'fields': ('username', 'email', 'password1', 'password2', 'rol', 'activo'),
        }),
    )
    
    # Configuración adicional
    filter_horizontal = ('groups', 'user_permissions')
