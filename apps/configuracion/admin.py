"""
apps/configuracion/admin.py
FASE 2: Ya no restringe a un solo registro. Ahora permite una config por sucursal.
"""
from django.contrib import admin
from .models import AccesoRapidoPOS, ConfiguracionNegocio


@admin.register(ConfiguracionNegocio)
class ConfiguracionNegocioAdmin(admin.ModelAdmin):
    list_display = ('nombre_negocio', 'sucursal', 'fecha_modificacion')
    list_filter = ('sucursal',)

    fieldsets = (
        ('Sucursal', {
            'fields': ('sucursal',),
            'description': 'Sucursal a la que pertenece esta configuracion.'
        }),
        ('Identidad del Negocio', {
            'fields': ('nombre_negocio', 'rnc', 'direccion', 'telefono', 'email_negocio', 'logo')
        }),
        ('Modulos', {
            'fields': (
                'modulo_etiquetas_zebra', 'modulo_financiacion_coop',
                'modulo_cotizaciones', 'modulo_impresion_termica',
                'modulo_barcode_scanner', 'modulo_reportes_ondemand',
                'modulo_ecf', 'modulo_dashboard',
                'permitir_inventario_negativo',
            )
        }),
        ('Metodos de Pago', {
            'fields': ('pago_efectivo', 'pago_transferencia', 'pago_tarjeta')
        }),
        ('Parametros Operativos', {
            'fields': (
                'formato_codigo_barras', 'dias_anulacion',
                'cantidad_copias_ticket',
            )
        }),
    )

    # -----------------------------------------------------------------
    # CFG-003: el Admin es la UNICA interfaz de esta configuracion, y operaba
    # en un plano de permisos distinto del RBAC del negocio.
    #
    # `configuracion.administrar` esta declarado en el catalogo y no tenia
    # ningun consumidor: un staff con el permiso Django `change_configuracionnegocio`
    # —otorgado para una necesidad puntual— abria el changelist con 200 y veia
    # las configuraciones de TODAS las sucursales. Y al reves: el panel RBAC
    # comunicaba una capacidad que en la practica no habilitaba ni revocaba
    # nada.
    #
    # Ahora Admin exige AMBOS: el permiso Django (que ya pedia) y el permiso
    # RBAC, y ademas el queryset se acota a las sucursales del alcance.
    # -----------------------------------------------------------------

    PERMISO_RBAC = 'configuracion.administrar'

    def _alcance(self, request):
        from apps.permisos.alcance import Alcance
        from apps.permisos.engine import sucursales_con_permiso

        ids = sucursales_con_permiso(request.user, self.PERMISO_RBAC)
        return Alcance(ids, consolidado=ids is None)

    def _autorizado(self, request):
        return self._alcance(request).permitido

    def get_queryset(self, request):
        return self._alcance(request).filtrar(super().get_queryset(request))

    def has_module_permission(self, request):
        return super().has_module_permission(request) and self._autorizado(request)

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj) and self._autorizado(request)

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and self._autorizado(request)

    def has_add_permission(self, request):
        # Fase 2: permitir multiples configs (una por sucursal), con RBAC.
        return self._autorizado(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AccesoRapidoPOS)
class AccesoRapidoPOSAdmin(admin.ModelAdmin):
    list_display = (
        'orden',
        'etiqueta_visible',
        'tipo',
        'producto',
        'categoria',
        'color',
        'activo',
        'fecha_modificacion',
    )
    list_filter = ('tipo', 'activo', 'color')
    list_display_links = ('etiqueta_visible',)
    search_fields = (
        'etiqueta',
        'producto__nombre',
        'producto__sku',
        'producto__codigo_barras',
        'categoria__nombre',
    )
    autocomplete_fields = ('producto', 'categoria')
    list_editable = ('orden', 'activo')
    ordering = ('orden', 'id')
    fieldsets = (
        ('Boton', {
            'fields': ('etiqueta', 'tipo', 'color', 'orden', 'activo')
        }),
        ('Destino', {
            'fields': ('producto', 'categoria'),
            'description': 'Use producto para agregar al carrito; use categoria para filtrar resultados en el POS.',
        }),
    )
