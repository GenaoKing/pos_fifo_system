"""
apps/sync/events.py

Funciones helper que crean EventoSync desde las vistas de la app.

DISENO: opcion B del roadmap (explicit call, no signals).

Uso recomendado en las vistas:

    from django.db import transaction
    from apps.sync import events as sync_events

    with transaction.atomic():
        venta = Venta.objects.create(...)
        # ... crear detalles, pagos, etc ...
        transaction.on_commit(lambda: sync_events.evento_venta_creada(venta))

Estas funciones SIEMPRE son no-throw. Capturan sus excepciones y las loguean.
No queremos que un fallo al encolar sync tumbe la operacion principal.
"""
import hashlib
import json
import logging

from django.conf import settings

from . import serializers

logger = logging.getLogger('sync')


def _calcular_hash(payload):
    """SHA-256 del payload serializado (sort_keys para determinismo)."""
    serialized = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def _crear_evento(tipo, payload, referencia='', objeto_id_local=None, sucursal=None):
    """Core: crea un EventoSync. Nunca lanza excepcion, solo loguea."""
    if not getattr(settings, 'SYNC_ENABLED', False):
        return None

    try:
        from .models import EventoSync
        from apps.sucursales.models import get_sucursal_actual

        if sucursal is None:
            sucursal = get_sucursal_actual()

        evento = EventoSync.objects.create(
            sucursal=sucursal,
            tipo_evento=tipo,
            objeto_referencia=referencia or '',
            objeto_id_local=objeto_id_local,
            payload=payload,
            hash_payload=_calcular_hash(payload),
            estado='PENDIENTE',
        )
        logger.info(
            'Evento sync encolado: %s %s (id=%s)',
            tipo, referencia, evento.id
        )
        return evento
    except Exception as exc:
        logger.exception('Error encolando evento sync %s %s: %s', tipo, referencia, exc)
        return None


# ============================================================================
# HELPERS PUBLICOS - llamar desde las vistas via transaction.on_commit
# ============================================================================

def evento_venta_creada(venta):
    """Encola un evento VENTA_CREADA."""
    try:
        payload = serializers.serializar_venta(venta)
    except Exception as exc:
        logger.exception('No se pudo serializar venta %s: %s', venta.pk, exc)
        return None

    return _crear_evento(
        tipo='VENTA_CREADA',
        payload=payload,
        referencia=venta.numero_venta,
        objeto_id_local=venta.pk,
        sucursal=venta.sucursal,
    )


def evento_venta_anulada(venta):
    """Encola un evento VENTA_ANULADA."""
    try:
        payload = serializers.serializar_anulacion_venta(venta)
    except Exception as exc:
        logger.exception('No se pudo serializar anulacion venta %s: %s', venta.pk, exc)
        return None

    return _crear_evento(
        tipo='VENTA_ANULADA',
        payload=payload,
        referencia=venta.numero_venta,
        objeto_id_local=venta.pk,
        sucursal=venta.sucursal,
    )


def evento_apertura_caja(turno):
    """Encola un evento APERTURA_CAJA."""
    try:
        payload = serializers.serializar_apertura_caja(turno)
    except Exception as exc:
        logger.exception('No se pudo serializar apertura turno %s: %s', turno.pk, exc)
        return None

    sucursal = turno.caja.sucursal if turno.caja.sucursal_id else None
    return _crear_evento(
        tipo='APERTURA_CAJA',
        payload=payload,
        referencia=f'Turno-{turno.pk}',
        objeto_id_local=turno.pk,
        sucursal=sucursal,
    )


def evento_movimiento_caja(movimiento):
    """Encola un evento MOVIMIENTO_CAJA (retiro, gasto o ingreso)."""
    try:
        payload = serializers.serializar_movimiento_caja(movimiento)
    except Exception as exc:
        logger.exception('No se pudo serializar movimiento %s: %s', movimiento.pk, exc)
        return None

    turno = movimiento.turno
    sucursal = turno.caja.sucursal if turno.caja.sucursal_id else None
    return _crear_evento(
        tipo='MOVIMIENTO_CAJA',
        payload=payload,
        referencia=f'Mov-{movimiento.pk}-{movimiento.tipo}',
        objeto_id_local=movimiento.pk,
        sucursal=sucursal,
    )


def evento_cierre_caja(turno):
    """Encola un evento CIERRE_CAJA."""
    try:
        payload = serializers.serializar_cierre_caja(turno)
    except Exception as exc:
        logger.exception('No se pudo serializar cierre turno %s: %s', turno.pk, exc)
        return None

    sucursal = turno.caja.sucursal if turno.caja.sucursal_id else None
    return _crear_evento(
        tipo='CIERRE_CAJA',
        payload=payload,
        referencia=f'Turno-{turno.pk}',
        objeto_id_local=turno.pk,
        sucursal=sucursal,
    )


def evento_ajuste_inventario(ajuste):
    """Encola un evento AJUSTE_INVENTARIO."""
    try:
        payload = serializers.serializar_ajuste_inventario(ajuste)
    except Exception as exc:
        logger.exception('No se pudo serializar ajuste %s: %s', ajuste.pk, exc)
        return None

    return _crear_evento(
        tipo='AJUSTE_INVENTARIO',
        payload=payload,
        referencia=f'Ajuste-{ajuste.pk}',
        objeto_id_local=ajuste.pk,
    )


def evento_compra_registrada(compra):
    """Encola un evento COMPRA_REGISTRADA."""
    try:
        payload = serializers.serializar_compra(compra)
    except Exception as exc:
        logger.exception('No se pudo serializar compra %s: %s', compra.pk, exc)
        return None

    return _crear_evento(
        tipo='COMPRA_REGISTRADA',
        payload=payload,
        referencia=getattr(compra, 'numero_compra', '') or f'Compra-{compra.pk}',
        objeto_id_local=compra.pk,
    )