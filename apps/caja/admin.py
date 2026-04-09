from django.contrib import admin
from .models import Caja, TurnoCaja, MovimientoCaja


@admin.register(Caja)
class CajaAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'activa', 'fecha_creacion']
    list_filter = ['activa']


@admin.register(TurnoCaja)
class TurnoCajaAdmin(admin.ModelAdmin):
    list_display = ['caja', 'usuario', 'fecha_apertura', 'fecha_cierre', 'estado', 'fondo_apertura', 'monto_esperado', 'monto_contado', 'diferencia']
    list_filter = ['estado', 'caja']
    search_fields = ['usuario__username', 'usuario__first_name']
    readonly_fields = ['monto_esperado', 'diferencia']


@admin.register(MovimientoCaja)
class MovimientoCajaAdmin(admin.ModelAdmin):
    list_display = ['turno', 'tipo', 'monto', 'descripcion', 'registrado_por', 'autorizado_por', 'fecha']
    list_filter = ['tipo']
    search_fields = ['descripcion']