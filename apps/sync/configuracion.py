"""Contrato interno del pull de ``ConfiguracionNegocio``.

El transporte sigue enviando la forma legacy (campos ``modulo_*``) para que
un POS instalado no tenga que negociar un esquema nuevo. El cloud calcula esos
flags desde el mismo motor de capacidades que aplica los gates; el POS valida
todo el conjunto entrante antes de persistirlo.
"""
from copy import copy

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.configuracion.models import ConfiguracionNegocio


CONFIGURACION_SYNC_FIELDS = (
    'nombre_negocio',
    'rnc',
    'direccion',
    'telefono',
    'email_negocio',
    'permitir_inventario_negativo',
    'modulo_etiquetas_zebra',
    'modulo_financiacion_coop',
    'modulo_cotizaciones',
    'modulo_impresion_termica',
    'modulo_barcode_scanner',
    'modulo_reportes_ondemand',
    'modulo_ecf',
    'modulo_dashboard',
    'pago_efectivo',
    'pago_transferencia',
    'pago_tarjeta',
    'formato_codigo_barras',
    'dias_anulacion',
    'cantidad_copias_ticket',
    'ecf_proveedor',
    'itbis_incluido_en_precio',
    'itbis_porcentaje_global',
    'modo_contingencia',
)


class ConfiguracionPullSerializer(serializers.ModelSerializer):
    """Valida tipos y limites de la allowlist, aceptando payloads parciales.

    Los payloads de nubes anteriores pueden omitir campos agregados despues;
    por eso todos son opcionales. Los campos fuera de la allowlist conservan
    el comportamiento historico: no se aplican en un POS local.
    """

    class Meta:
        model = ConfiguracionNegocio
        fields = CONFIGURACION_SYNC_FIELDS
        extra_kwargs = {
            field: {'required': False}
            for field in CONFIGURACION_SYNC_FIELDS
        }


def validar_payload_configuracion(config, payload):
    """Devuelve los cambios ya validados, sin mutar ``config``.

    Ademas de los tipos de DRF, se ejecuta ``full_clean`` sobre una copia con
    los valores entrantes. Asi las reglas cruzadas de CFG-006 se evaluan antes
    de cualquier ``save`` y un payload no puede dejar una configuracion a medio
    actualizar.
    """
    from apps.sync.engine import PayloadPullInvalido

    if not isinstance(payload, dict):
        raise PayloadPullInvalido(
            'configuracion invalida: se esperaba un objeto JSON.'
        )

    datos = {
        field: payload[field]
        for field in CONFIGURACION_SYNC_FIELDS
        if field in payload
    }
    serializer = ConfiguracionPullSerializer(data=datos, partial=True)
    if not serializer.is_valid():
        raise PayloadPullInvalido(
            f'configuracion invalida: {serializer.errors}'
        )

    candidata = copy(config)
    for field, value in serializer.validated_data.items():
        setattr(candidata, field, value)
    try:
        candidata.full_clean()
    except DjangoValidationError as exc:
        raise PayloadPullInvalido(f'configuracion invalida: {exc.message_dict}') from exc

    return serializer.validated_data


def flags_legacy_efectivos(config, sucursal):
    """Deriva cada flag legacy cuando existe una autoridad comercial.

    Una instalacion legacy sin negocio conserva sus flags crudos: el engine es
    fail-open sin negocio y usarlo ahi cambiaria la conducta de un POS aun no
    aprovisionado en suscripciones.
    """
    from apps.suscripciones import registry

    negocio = getattr(sucursal, 'negocio', None)
    if negocio is None:
        return {
            modulo.flag_legacy: getattr(config, modulo.flag_legacy)
            for modulo in registry.CATALOGO_MODULOS
            if modulo.flag_legacy
        }

    from apps.suscripciones.engine import modulo_activo

    return {
        modulo.flag_legacy: modulo_activo(
            modulo.key, negocio=negocio, sucursal=sucursal,
        )
        for modulo in registry.CATALOGO_MODULOS
        if modulo.flag_legacy
    }
