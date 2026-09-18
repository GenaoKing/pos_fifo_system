"""Mutaciones de configuración de negocio expuestas al portal."""
from django.db import transaction

from apps.auditoria.models import Auditoria
from apps.auditoria.services import registrar_mutacion

from .models import ConfiguracionNegocio


class ConfiguracionMutationError(ValueError):
    pass


# Los módulos comerciales se resuelven por suscripción; e-CF, logo y emisor
# tienen flujos separados. Esta allowlist es la frontera del panel C04 p5.2.
CAMPOS_PORTAL_EDITABLES = {
    'nombre_negocio', 'rnc', 'direccion', 'telefono', 'email_negocio',
    'permitir_inventario_negativo',
    'pago_efectivo', 'pago_transferencia', 'pago_tarjeta',
    'formato_codigo_barras', 'dias_anulacion', 'cantidad_copias_ticket',
    'conteo_ciego_caja',
    'descuento_requiere_autorizacion', 'descuento_tolerancia_monto',
    'descuento_tolerancia_porcentaje', 'descuento_motivo_modo',
    'descuento_vigencia_minutos',
    'itbis_incluido_en_precio', 'itbis_porcentaje_global', 'modo_contingencia',
}


def _db_de(obj):
    return getattr(getattr(obj, '_state', None), 'db', None)


def _tenant_auditoria(negocio, using):
    if using.startswith('tnt_'):
        from apps.tenancy.context import get_current_tenant_key

        return get_current_tenant_key()
    return negocio.slug if negocio is not None else None


def snapshot_configuracion(configuracion):
    return {
        campo: getattr(configuracion, campo)
        for campo in sorted(CAMPOS_PORTAL_EDITABLES)
    }


def _autorizar(actor, negocio, using):
    if actor is None or _db_de(actor) != using:
        raise ConfiguracionMutationError('El actor debe pertenecer a la BD tenant.')
    if not getattr(actor, 'activo', False):
        raise ConfiguracionMutationError('El actor esta inactivo.')
    if not getattr(actor, 'is_superuser', False) and actor.negocio_id != negocio.pk:
        raise ConfiguracionMutationError('El actor pertenece a otro negocio.')
    # Los flags que esta ruta controla afectan al negocio completo: no se
    # permite una asignacion de rol limitada a una sucursal.
    if not actor.tiene_permiso('configuracion.administrar', sucursal=None):
        raise ConfiguracionMutationError(
            'Sin permiso global configuracion.administrar.'
        )


def actualizar_configuracion(
    *, configuracion_id, actor, cambios, motivo, correlacion_id=None, using,
):
    """Aplica la allowlist portal, valida el modelo y deja CT-01 atomico."""
    desconocidos = set(cambios) - CAMPOS_PORTAL_EDITABLES
    if desconocidos:
        raise ConfiguracionMutationError(
            f'Campos no soportados por configuracion portal: {sorted(desconocidos)}.'
        )
    if not cambios:
        raise ConfiguracionMutationError('Debe enviar al menos un campo editable.')
    if not motivo or not motivo.strip():
        raise ConfiguracionMutationError('motivo es obligatorio.')

    with transaction.atomic(using=using):
        configuracion = (
            ConfiguracionNegocio.objects.using(using).select_for_update()
            .get(pk=configuracion_id)
        )
        if configuracion.sucursal_id is None or configuracion.sucursal.negocio_id is None:
            raise ConfiguracionMutationError(
                'La configuracion no esta asociada a una sucursal de negocio.'
            )
        _autorizar(actor, configuracion.sucursal.negocio, using)
        antes = snapshot_configuracion(configuracion)
        for campo, valor in cambios.items():
            setattr(configuracion, campo, valor)
        configuracion.full_clean()
        despues = snapshot_configuracion(configuracion)
        if antes == despues:
            return configuracion
        configuracion.save(
            using=using,
            update_fields=[*cambios, 'fecha_modificacion'],
        )
        registrar_mutacion(
            accion='configuracion.configuracion.actualizada',
            actor=actor,
            entidad=configuracion,
            antes=antes,
            despues=despues,
            resultado=Auditoria.Resultado.SUCCEEDED,
            canal=Auditoria.Canal.PORTAL_API,
            tenant=_tenant_auditoria(configuracion.sucursal.negocio, using),
            sucursal=configuracion.sucursal,
            correlacion_id=correlacion_id,
            metadata={'motivo': motivo.strip()},
            using=using,
        )
    return configuracion
