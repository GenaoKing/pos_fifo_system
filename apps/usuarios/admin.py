from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db import router

from apps.auditoria.models import Auditoria
from apps.auditoria.services import registrar_mutacion
from .models import Usuario


@admin.register(Usuario)
class UsuarioAdmin(BaseUserAdmin):
    """Administración de usuarios en Django Admin"""
    
    # Campos a mostrar en la lista
    list_display = ('username', 'email', 'negocio', 'rol', 'activo', 'fecha_creacion')
    list_filter = ('rol', 'activo', 'is_staff', 'negocio', 'fecha_creacion')
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
            # `negocio` es visible y editable: el admin lo omitia por completo,
            # asi que un alta desde aca quedaba con `negocio=NULL` — el valor
            # que los resolutores leen como "identidad global" (USR-003).
            'fields': ('negocio', 'rol', 'activo', 'is_staff', 'is_superuser',
                       'groups', 'user_permissions')
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
            'fields': ('username', 'email', 'negocio', 'password1', 'password2',
                       'rol', 'activo'),
        }),
    )
    
    # Configuración adicional
    filter_horizontal = ('groups', 'user_permissions')
    actions = None

    def save_model(self, request, obj, form, change):
        """El Admin local comparte CT-01; en cloud la URL no existe."""
        using = obj._state.db or router.db_for_write(type(obj)) or 'default'
        antes = {}
        if change:
            original = type(obj).objects.using(using).get(pk=obj.pk)
            antes = {
                'username': original.username,
                'email': original.email,
                'rol_legacy': original.rol,
                'activo': original.activo,
                'is_staff': original.is_staff,
                'is_superuser': original.is_superuser,
                'negocio_id': original.negocio_id,
            }

        super().save_model(request, obj, form, change)
        despues = {
            'username': obj.username,
            'email': obj.email,
            'rol_legacy': obj.rol,
            'activo': obj.activo,
            'is_staff': obj.is_staff,
            'is_superuser': obj.is_superuser,
            'negocio_id': obj.negocio_id,
            'password_changed': 'password' in getattr(form, 'changed_data', ()),
            'credential_scope': 'LOCAL_POS',
        }
        registrar_mutacion(
            accion=(
                'usuarios.usuario.actualizado'
                if change else 'usuarios.usuario.creado'
            ),
            actor=request.user,
            entidad=obj,
            antes=antes,
            despues=despues,
            resultado=Auditoria.Resultado.SUCCEEDED,
            canal=Auditoria.Canal.POS_LOCAL,
            tenant=obj.negocio.slug if obj.negocio_id else None,
            metadata={
                'source': 'DJANGO_ADMIN_LOCAL',
                'credential_policy': 'LOCAL_POS_SEPARATE_FROM_PORTAL',
            },
            using=using,
        )

    def has_delete_permission(self, request, obj=None):
        # Baja = activo=False por el servicio; no se borra identidad historica.
        return False

    def has_add_permission(self, request):
        # El alta Admin no puede crear atomicamente AsignacionRol + CT-01. El
        # camino soportado es provisionar_usuario_tenant.
        return False
