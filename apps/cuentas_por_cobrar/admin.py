"""
apps/cuentas_por_cobrar/admin.py

Admin de CxC en modo SOLO LECTURA.

Las invariantes de este modulo —bloqueo de fila, aplicacion FIFO a cuotas,
reversa LIFO, auditoria y outbox— viven en `services.py`, no en metodos
inevitables del modelo. Editar una cuenta, una cuota o un pago desde el admin
las saltaba todas: se podia dejar el saldo de la cuenta distinto de la suma de
sus cuotas, alterar un pago ya aplicado o crear movimientos que ningun evento
replicaria al cloud. El log generico del admin no sustituye la auditoria
financiera del dominio.

Para operar: cobrar y anular abonos desde la interfaz de CxC, que pasa por los
services.
"""
from django.contrib import admin

from .models import CuentaPorCobrar, CuotaCxC, MetodoPlazoCredito, PagoCxC


class SoloLecturaMixin:
    """Bloquea alta, edicion y borrado de registros financieros."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MetodoPlazoCredito)
class MetodoPlazoCreditoAdmin(admin.ModelAdmin):
    """
    Configuracion comercial: SI es editable.

    A diferencia de cuentas, cuotas y pagos, un metodo de plazo es un catalogo,
    no un hecho contable ya ocurrido. Cambiarlo afecta ventas futuras, no
    reescribe deuda existente.
    """
    list_display = (
        'nombre', 'tipo', 'sucursal', 'dias_vencimiento', 'cantidad_cuotas',
        'inicial_minima_porcentaje', 'interes_porcentaje', 'activo',
    )
    list_filter = ('tipo', 'activo', 'sucursal')
    search_fields = ('nombre',)


class CuotaCxCInline(SoloLecturaMixin, admin.TabularInline):
    model = CuotaCxC
    extra = 0
    fields = ('numero', 'monto', 'saldo', 'fecha_vencimiento', 'estado', 'fecha_pago')
    readonly_fields = fields
    can_delete = False


class PagoCxCInline(SoloLecturaMixin, admin.TabularInline):
    model = PagoCxC
    extra = 0
    fields = (
        'metodo', 'monto', 'referencia', 'fecha_pago', 'registrado_por',
        'estado', 'aplicaciones',
    )
    readonly_fields = fields
    can_delete = False


@admin.register(CuentaPorCobrar)
class CuentaPorCobrarAdmin(SoloLecturaMixin, admin.ModelAdmin):
    list_display = (
        'venta', 'cliente', 'sucursal', 'total', 'saldo_original',
        'monto_interes', 'saldo', 'estado', 'fecha_limite',
    )
    list_filter = ('estado', 'metodo_plazo', 'sucursal')
    search_fields = ('venta__numero_venta', 'cliente__nombre', 'cliente__cedula_rnc')
    # Todo el registro es historico: cliente, venta, importes, estado y la
    # autorizacion del override quedaban editables.
    readonly_fields = (
        'venta', 'cliente', 'sucursal', 'metodo_plazo', 'total', 'monto_inicial',
        'saldo_original', 'interes_porcentaje', 'monto_interes', 'saldo',
        'estado', 'fecha_emision', 'fecha_limite', 'creado_por',
        'override_autorizado_por', 'motivo_override',
        'fecha_creacion', 'fecha_modificacion',
    )
    inlines = (CuotaCxCInline, PagoCxCInline)


@admin.register(CuotaCxC)
class CuotaCxCAdmin(SoloLecturaMixin, admin.ModelAdmin):
    list_display = ('cuenta', 'numero', 'monto', 'saldo', 'fecha_vencimiento', 'estado')
    list_filter = ('estado', 'fecha_vencimiento')
    search_fields = ('cuenta__venta__numero_venta', 'cuenta__cliente__nombre')


@admin.register(PagoCxC)
class PagoCxCAdmin(SoloLecturaMixin, admin.ModelAdmin):
    list_display = ('cuenta', 'metodo', 'monto', 'fecha_pago', 'registrado_por', 'estado')
    list_filter = ('metodo', 'estado', 'fecha_pago')
    search_fields = ('cuenta__venta__numero_venta', 'cuenta__cliente__nombre', 'referencia')
