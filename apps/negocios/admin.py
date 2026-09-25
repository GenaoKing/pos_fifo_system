from django.contrib import admin
from django.db import router

from apps.auditoria.models import Auditoria
from apps.auditoria.services import registrar_mutacion

from .models import Negocio


@admin.register(Negocio)
class NegocioAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'slug', 'rnc', 'activo', 'fecha_creacion')
    list_filter = ('activo',)
    search_fields = ('nombre', 'slug', 'rnc')
    readonly_fields = ('fecha_creacion', 'fecha_modificacion')
    actions = None

    def get_readonly_fields(self, request, obj=None):
        campos = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            campos.append('slug')
        return tuple(campos)

    def save_model(self, request, obj, form, change):
        using = obj._state.db or router.db_for_write(type(obj)) or 'default'
        antes = {}
        if change:
            original = type(obj).objects.using(using).get(pk=obj.pk)
            antes = {
                'nombre': original.nombre,
                'slug': original.slug,
                'rnc': original.rnc_canonico,
                'activo': original.activo,
            }
        super().save_model(request, obj, form, change)
        despues = {
            'nombre': obj.nombre,
            'slug': obj.slug,
            'rnc': obj.rnc_canonico,
            'activo': obj.activo,
        }
        registrar_mutacion(
            accion=(
                'negocios.negocio.actualizado'
                if change else 'negocios.negocio.creado'
            ),
            actor=request.user,
            entidad=obj,
            antes=antes,
            despues=despues,
            resultado=Auditoria.Resultado.SUCCEEDED,
            canal=Auditoria.Canal.POS_LOCAL,
            tenant=obj.slug,
            metadata={'source': 'DJANGO_ADMIN_LOCAL'},
            using=using,
        )

    def has_delete_permission(self, request, obj=None):
        # Retiro normal = desactivar; la purga con cascadas no vive en Admin.
        return False
