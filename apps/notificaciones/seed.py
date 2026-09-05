"""Defaults idempotentes para tenants nuevos o reaprovisionados."""


def crear_reglas_default(negocio):
    from apps.permisos.models import Rol

    from .models import ReglaNotificacionRol

    creadas = 0
    for rol in Rol.objects.filter(
        negocio=negocio, es_sistema=True, slug='administrador',
    ):
        for tipo_evento in ('caja.apertura', 'caja.cierre'):
            _, creada = ReglaNotificacionRol.objects.get_or_create(
                rol=rol,
                tipo_evento=tipo_evento,
                defaults={'activa': True, 'enviar_push': True, 'parametros': {}},
            )
            creadas += int(creada)
    return creadas
