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

    sucursal = ajuste.lote.sucursal if ajuste.lote.sucursal_id else None
    return _crear_evento(
        tipo='AJUSTE_INVENTARIO',
        payload=payload,
        referencia=f'Ajuste-{ajuste.pk}',
        objeto_id_local=ajuste.pk,
        sucursal=sucursal,
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
        sucursal=compra.sucursal,
    )


def evento_inventario_movimiento(movimiento):
    """Encola un movimiento de inventario como ledger cloud."""
    try:
        payload = serializers.serializar_movimiento_inventario(movimiento)
    except Exception as exc:
        logger.exception('No se pudo serializar movimiento inventario %s: %s', movimiento.pk, exc)
        return None

    sucursal = movimiento.lote.sucursal if movimiento.lote.sucursal_id else None
    return _crear_evento(
        tipo='INVENTARIO_MOVIMIENTO_REGISTRADO',
        payload=payload,
        referencia=f"MovInv-{movimiento.pk}-{movimiento.tipo}",
        objeto_id_local=movimiento.pk,
        sucursal=sucursal,
    )


def evento_inventario_snapshot(sucursal=None):
    """Encola un snapshot completo del inventario local actual."""
    try:
        payload = serializers.serializar_inventario_snapshot(sucursal=sucursal)
    except Exception as exc:
        logger.exception('No se pudo serializar snapshot inventario: %s', exc)
        return None

    return _crear_evento(
        tipo='INVENTARIO_SNAPSHOT',
        payload=payload,
        referencia=f"Snapshot-{payload.get('sucursal_codigo') or 'LOCAL'}",
        sucursal=sucursal,
    )


def evento_cotizacion_creada(cotizacion):
    """Encola un evento COTIZACION_CREADA."""
    try:
        payload = serializers.serializar_cotizacion(cotizacion)
    except Exception as exc:
        logger.exception('No se pudo serializar cotizacion %s: %s', cotizacion.pk, exc)
        return None

    return _crear_evento(
        tipo='COTIZACION_CREADA',
        payload=payload,
        referencia=cotizacion.numero_cotizacion,
        objeto_id_local=cotizacion.pk,
        sucursal=cotizacion.sucursal,
    )


def evento_cotizacion_convertida(cotizacion):
    """Encola un evento COTIZACION_CONVERTIDA."""
    try:
        payload = serializers.serializar_cotizacion(cotizacion)
    except Exception as exc:
        logger.exception('No se pudo serializar conversion cotizacion %s: %s', cotizacion.pk, exc)
        return None

    return _crear_evento(
        tipo='COTIZACION_CONVERTIDA',
        payload=payload,
        referencia=cotizacion.numero_cotizacion,
        objeto_id_local=cotizacion.pk,
        sucursal=cotizacion.sucursal,
    )


def evento_cxc_creada(cuenta):
    """Encola un evento CXC_CREADA."""
    try:
        payload = serializers.serializar_cxc(cuenta)
    except Exception as exc:
        logger.exception('No se pudo serializar CxC %s: %s', cuenta.pk, exc)
        return None

    return _crear_evento(
        tipo='CXC_CREADA',
        payload=payload,
        referencia=cuenta.venta.numero_venta,
        objeto_id_local=cuenta.pk,
        sucursal=cuenta.sucursal,
    )


def evento_cxc_pago_registrado(pago):
    """Encola un evento CXC_PAGO_REGISTRADO."""
    try:
        payload = serializers.serializar_pago_cxc(pago)
    except Exception as exc:
        logger.exception('No se pudo serializar pago CxC %s: %s', pago.pk, exc)
        return None

    return _crear_evento(
        tipo='CXC_PAGO_REGISTRADO',
        payload=payload,
        referencia=f'{pago.cuenta.venta.numero_venta}-P{pago.pk}',
        objeto_id_local=pago.pk,
        sucursal=pago.cuenta.sucursal,
    )


def evento_cxc_pago_anulado(pago):
    """Encola un evento CXC_PAGO_ANULADO."""
    try:
        payload = serializers.serializar_anulacion_pago_cxc(pago)
    except Exception as exc:
        logger.exception('No se pudo serializar anulacion de pago CxC %s: %s', pago.pk, exc)
        return None

    return _crear_evento(
        tipo='CXC_PAGO_ANULADO',
        payload=payload,
        referencia=f'{pago.cuenta.venta.numero_venta}-P{pago.pk}-ANUL',
        objeto_id_local=pago.pk,
        sucursal=pago.cuenta.sucursal,
    )


def evento_cxc_anulada(cuenta):
    """Encola un evento CXC_ANULADA."""
    try:
        payload = serializers.serializar_cxc(cuenta)
    except Exception as exc:
        logger.exception('No se pudo serializar anulacion CxC %s: %s', cuenta.pk, exc)
        return None

    return _crear_evento(
        tipo='CXC_ANULADA',
        payload=payload,
        referencia=cuenta.venta.numero_venta,
        objeto_id_local=cuenta.pk,
        sucursal=cuenta.sucursal,
    )
