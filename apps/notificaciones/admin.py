from django.contrib import admin

from .models import (
    DestinatarioNotificacion,
    EntregaPush,
    EventoNotificable,
    ExcepcionNotificacionUsuario,
    MotorNotificaciones,
    ReglaNotificacionRol,
    SuscripcionPush,
)


admin.site.register(MotorNotificaciones)
admin.site.register(ReglaNotificacionRol)
admin.site.register(ExcepcionNotificacionUsuario)
@admin.register(EventoNotificable)
class EventoNotificableAdmin(admin.ModelAdmin):
    """Los hechos se inspeccionan, pero nunca se editan desde el admin."""

    readonly_fields = [field.name for field in EventoNotificable._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(DestinatarioNotificacion)
admin.site.register(SuscripcionPush)
admin.site.register(EntregaPush)
