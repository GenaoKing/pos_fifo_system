"""
apps/caja/admin.py

Admin de caja: el catalogo es editable, la historia NO.

Turnos y movimientos son hechos de efectivo ya ocurridos, con auditoria y
evento de sync asociados. Editarlos desde el admin saltaba todas las
invariantes: se podia cambiar el fondo de apertura o el monto contado de un
turno ya cerrado, reescribir el importe de un retiro, o alterar
`autorizado_por` — sin auditoria de dominio y sin que ningun evento replicara
el cambio al cloud. El arqueo dejaba de significar algo.
"""
from django.contrib import admin

from .models import Caja, MovimientoCaja, TurnoCaja


class SoloLecturaMixin:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Caja)
class CajaAdmin(admin.ModelAdmin):
    """
    Catalogo de cajas fisicas: SI es editable.

    Dar de alta una caja o desactivarla es configuracion, no un movimiento de
    efectivo.
    """
    list_display = ['nombre', 'sucursal', 'activa', 'fecha_creacion']
    list_filter = ['activa', 'sucursal']


@admin.register(TurnoCaja)
class TurnoCajaAdmin(SoloLecturaMixin, admin.ModelAdmin):
    list_display = [
        'caja', 'usuario', 'fecha_apertura', 'fecha_cierre', 'estado',
        'fondo_apertura', 'monto_esperado', 'monto_contado', 'diferencia',
    ]
    list_filter = ['estado', 'caja']
    search_fields = ['usuario__username', 'usuario__first_name']
    readonly_fields = [
        'caja', 'usuario', 'fecha_apertura', 'fecha_cierre', 'estado',
        'fondo_apertura', 'monto_esperado', 'monto_contado', 'diferencia',
        'cerrado_por', 'notas_apertura', 'notas_cierre',
    ]


@admin.register(MovimientoCaja)
class MovimientoCajaAdmin(SoloLecturaMixin, admin.ModelAdmin):
    list_display = [
        'turno', 'tipo', 'monto', 'descripcion', 'registrado_por',
        'autorizado_por', 'fecha',
    ]
    list_filter = ['tipo']
    search_fields = ['descripcion']
    readonly_fields = [
        'turno', 'tipo', 'monto', 'descripcion', 'registrado_por',
        'autorizado_por', 'fecha',
    ]
