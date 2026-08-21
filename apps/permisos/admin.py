from django import forms
from django.contrib import admin

from .models import AsignacionRol, CredencialFisica, Permiso, Rol


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


class CredencialFisicaForm(forms.ModelForm):
    """
    Alta de una credencial. El campo de captura NO es el que se persiste: se
    hashea y se descarta, igual que una contrasena. Por eso una credencial no
    se puede "editar" para ver su codigo — solo darse de baja y emitir otra.
    """

    codigo = forms.CharField(
        label='Codigo de la credencial',
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'off'}),
        help_text=(
            'Pasa el carnet por el lector con el cursor en este campo. '
            'Solo se guarda su hash. Dejar vacio al editar mantiene el codigo actual.'
        ),
    )

    class Meta:
        model = CredencialFisica
        fields = ('usuario', 'etiqueta', 'activa')

    def clean_codigo(self):
        codigo = CredencialFisica.normalizar(self.cleaned_data.get('codigo'))
        if not codigo:
            if self.instance.pk:
                return ''
            raise forms.ValidationError('Escanea o escribe el codigo de la credencial.')

        if len(codigo) < CredencialFisica.LONGITUD_MINIMA:
            raise forms.ValidationError(
                f'El codigo debe tener al menos {CredencialFisica.LONGITUD_MINIMA} '
                f'caracteres. Un codigo corto se adivina.'
            )

        existente = CredencialFisica.objects.filter(
            codigo_hash=CredencialFisica.hash_codigo(codigo)
        ).exclude(pk=self.instance.pk).exists()
        if existente:
            raise forms.ValidationError('Esa credencial ya esta registrada.')

        return codigo

    def save(self, commit=True):
        instancia = super().save(commit=False)
        codigo = self.cleaned_data.get('codigo')
        if codigo:
            instancia.codigo_hash = CredencialFisica.hash_codigo(codigo)
        if commit:
            instancia.save()
        return instancia


@admin.register(CredencialFisica)
class CredencialFisicaAdmin(admin.ModelAdmin):
    form = CredencialFisicaForm
    list_display = ('etiqueta', 'usuario', 'activa', 'fecha_alta', 'fecha_baja')
    list_filter = ('activa',)
    search_fields = ('usuario__username', 'etiqueta')
    readonly_fields = ('fecha_alta', 'fecha_baja', 'creada_por')

    def save_model(self, request, obj, form, change):
        if not change:
            obj.creada_por = request.user
        super().save_model(request, obj, form, change)
