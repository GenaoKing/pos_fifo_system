"""
apps/sync/events.py

Funciones helper que crean EventoSync desde las vistas de la app.

DISENO: opcion B del roadmap (explicit call, no signals).

## El outbox es transaccional (Fase 1, 2026-08-19)

Estas funciones se llaman **DENTRO** de la transaccion de negocio, no en
`transaction.on_commit`:

    from django.db import transaction
    from apps.sync import events as sync_events

    with transaction.atomic():
        venta = Venta.objects.create(...)
        # ... crear detalles, pagos, etc ...
        sync_events.evento_venta_creada(venta)   # <-- misma transaccion

Si la transaccion hace rollback, el evento desaparece con ella. Esa es la
garantia que da el patron outbox y la razon de que exista: es imposible que
exista la venta sin su evento, o el evento sin su venta.

Historicamente esto vivia en `on_commit` (transaccion separada y posterior) y
ademas arrancaba con un `if not SYNC_ENABLED: return None`. La combinacion hizo
que un servicio arrancado sin esa variable guardara ventas sin encolar nada, en
silencio y sin posibilidad de reintento. Ver BUG-A en `docs/BUGS.md`.

**El gate de `SYNC_ENABLED` ya NO vive aqui**: se aplica al ENVIAR
(`SyncEngine.push_eventos` y el comando `sincronizar`). Una instalacion sin
cloud acumula eventos inertes y baratos; encender el sync mas tarde recupera el
historico en vez de perderlo.

Unica excepcion: `INVENTARIO_SNAPSHOT` (ver su docstring).

## Fallo de SERIALIZACION vs fallo de PERSISTENCIA

Son dos cosas distintas y se tratan distinto. La regla: un POS no puede perder
una venta porque falle un serializador, pero tampoco puede confirmar una venta
que el cloud nunca va a ver.

- **Serializacion**: si el serializador lanza, el evento se encola igual con
  `payload=NULL` y estado `SIN_PAYLOAD`. El push lo re-serializa desde la BD
  via `apps/sync/registry.py`. La venta sigue.
- **Persistencia**: si el INSERT del evento falla, se reintenta una vez como
  `SIN_PAYLOAD` (por si el problema era el payload en si). Si tampoco entra, la
  excepcion se PROPAGA y la transaccion de negocio revierte.

Ese segundo punto cambio en 2026-08-20. Antes cualquier fallo del INSERT se
capturaba y se devolvia `None`, con lo cual la operacion confirmaba sin nada
que reintentar: precisamente la perdida silenciosa que el patron outbox existe
para impedir. Un fallo de persistencia significa que la cola esta rota (tabla
ausente, esquema desfasado, disco), y en ese estado es preferible que la caja
pare a que facture contra un cloud que no se va a enterar.

Corolario operativo: si el POS empieza a rechazar ventas con un error de
`EventoSync`, correr `manage.py migrate` y `manage.py verificar_sync`.
"""
import hashlib
import json
import logging

from django.conf import settings
from django.db import IntegrityError, transaction

from . import serializers

logger = logging.getLogger('sync')


def _calcular_hash(payload):
    """SHA-256 del payload serializado (sort_keys para determinismo)."""
    serialized = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def _crear_evento(tipo, serializar, referencia='', objeto_id_local=None,
                  sucursal=None, solo_con_cloud=False):
    """
    Core: crea un EventoSync. Nunca lanza excepcion, solo loguea.

    `serializar` es un CALLABLE, no un payload ya construido: asi el fallo de
    serializacion se maneja aqui adentro y se degrada a SIN_PAYLOAD en vez de
    propagarse a la transaccion de negocio.

    `solo_con_cloud=True` mantiene el gate historico de SYNC_ENABLED para los
    eventos que no vale la pena acumular sin cloud.
    """
    if solo_con_cloud and not getattr(settings, 'SYNC_ENABLED', False):
        return None

    from .models import EventoSync
    from apps.sucursales.models import get_sucursal_actual

    if sucursal is None:
        try:
            sucursal = get_sucursal_actual()
        except Exception:
            logger.exception('No se pudo resolver la sucursal actual para %s', tipo)
            sucursal = None

    # 1) Intento normal: payload completo y hash listo para enviar.
    try:
        payload = serializar()
        hash_payload = _calcular_hash(payload)
        estado = 'PENDIENTE'
    except Exception as exc:
        # 2) Degradado: registramos QUE ocurrio el hecho aunque no sepamos
        #    serializarlo todavia. El push reintenta desde la BD.
        logger.exception(
            'No se pudo serializar %s %s (%s). Se encola como SIN_PAYLOAD.',
            tipo, referencia, exc,
        )
        payload = None
        hash_payload = ''
        estado = 'SIN_PAYLOAD'

    campos = dict(
        sucursal=sucursal,
        tipo_evento=tipo,
        objeto_referencia=referencia or '',
        objeto_id_local=objeto_id_local,
    )

    # Escalera de degradacion. Ver el bloque de docstring del modulo:
    #
    #   1) INSERT normal, con payload.
    #   2) Si el INSERT falla POR EL PAYLOAD (un tipo que el serializador
    #      resolvio pero JSONField no sabe guardar), reintenta como
    #      SIN_PAYLOAD. El push lo re-serializa desde la BD via registry.
    #   3) Si tampoco entra sin payload, el problema es de persistencia
    #      (tabla ausente, esquema desfasado, disco, permisos) y ahi SI se
    #      propaga: tragarselo rompia la unica garantia que justifica el
    #      outbox — que no exista el hecho de negocio sin su evento.
    try:
        evento = _insertar(EventoSync, campos, payload, hash_payload, estado)
    except IntegrityError as exc:
        # Hash repetido: el mismo hecho ya esta en la cola. No es un fallo,
        # es la constraint haciendo su trabajo (ver sync/migrations/0008).
        existente = EventoSync.objects.filter(hash_payload=hash_payload).first()
        if hash_payload and existente is not None:
            logger.info(
                'Evento sync %s %s ya estaba encolado (id=%s); no se duplica.',
                tipo, referencia, existente.id,
            )
            return existente
        logger.exception(
            'Error de integridad encolando %s %s: %s', tipo, referencia, exc,
        )
        raise
    except Exception as exc:
        if payload is None:
            # Ya estabamos en el intento degradado: es persistencia.
            logger.exception(
                'No se pudo persistir el evento sync %s %s: %s', tipo, referencia, exc,
            )
            raise

        logger.exception(
            'Fallo el INSERT de %s %s con payload (%s). Reintentando como '
            'SIN_PAYLOAD para no perder el hecho.',
            tipo, referencia, exc,
        )
        try:
            evento = _insertar(EventoSync, campos, None, '', 'SIN_PAYLOAD')
        except Exception as exc_degradado:
            # Segundo fallo: el problema no era el payload. Se propaga para
            # que la transaccion de negocio no confirme un hecho sin evento.
            logger.exception(
                'No se pudo persistir el evento sync %s %s ni siquiera sin '
                'payload: %s', tipo, referencia, exc_degradado,
            )
            raise
        estado = 'SIN_PAYLOAD'

    logger.info(
        'Evento sync encolado: %s %s (id=%s, estado=%s)',
        tipo, referencia, evento.id, estado,
    )
    return evento


def _insertar(EventoSync, campos, payload, hash_payload, estado):
    """
    INSERT del evento en su propio savepoint.

    El savepoint existe para que un INSERT fallido no deje abortada la
    transaccion de negocio (en Postgres cualquier sentencia fallida la invalida
    hasta el rollback), y asi poder reintentar degradado. NO es un "traga
    errores": quien llama decide que hacer con la excepcion.
    """
    with transaction.atomic():
        return EventoSync.objects.create(
            payload=payload,
            hash_payload=hash_payload,
            estado=estado,
            **campos,
        )


# ============================================================================
# HELPERS PUBLICOS - llamar DENTRO de la transaccion de negocio
# ============================================================================

def evento_venta_creada(venta):
    """Encola un evento VENTA_CREADA."""
    return _crear_evento(
        tipo='VENTA_CREADA',
        serializar=lambda: serializers.serializar_venta(venta),
        referencia=venta.numero_venta,
        objeto_id_local=venta.pk,
        sucursal=venta.sucursal,
    )


def evento_venta_anulada(venta):
    """Encola un evento VENTA_ANULADA."""
    return _crear_evento(
        tipo='VENTA_ANULADA',
        serializar=lambda: serializers.serializar_anulacion_venta(venta),
        referencia=venta.numero_venta,
        objeto_id_local=venta.pk,
        sucursal=venta.sucursal,
    )


def evento_apertura_caja(turno):
    """Encola un evento APERTURA_CAJA."""
    return _crear_evento(
        tipo='APERTURA_CAJA',
        serializar=lambda: serializers.serializar_apertura_caja(turno),
        referencia=f'Turno-{turno.pk}',
        objeto_id_local=turno.pk,
        sucursal=turno.caja.sucursal if turno.caja.sucursal_id else None,
    )


def evento_movimiento_caja(movimiento):
    """Encola un evento MOVIMIENTO_CAJA (retiro, gasto o ingreso)."""
    turno = movimiento.turno
    return _crear_evento(
        tipo='MOVIMIENTO_CAJA',
        serializar=lambda: serializers.serializar_movimiento_caja(movimiento),
        referencia=f'Mov-{movimiento.pk}-{movimiento.tipo}',
        objeto_id_local=movimiento.pk,
        sucursal=turno.caja.sucursal if turno.caja.sucursal_id else None,
    )


def evento_cierre_caja(turno):
    """Encola un evento CIERRE_CAJA."""
    return _crear_evento(
        tipo='CIERRE_CAJA',
        serializar=lambda: serializers.serializar_cierre_caja(turno),
        referencia=f'Turno-{turno.pk}',
        objeto_id_local=turno.pk,
        sucursal=turno.caja.sucursal if turno.caja.sucursal_id else None,
    )


def evento_ajuste_inventario(ajuste):
    """Encola un evento AJUSTE_INVENTARIO."""
    return _crear_evento(
        tipo='AJUSTE_INVENTARIO',
        serializar=lambda: serializers.serializar_ajuste_inventario(ajuste),
        referencia=f'Ajuste-{ajuste.pk}',
        objeto_id_local=ajuste.pk,
        sucursal=ajuste.lote.sucursal if ajuste.lote.sucursal_id else None,
    )


def evento_compra_registrada(compra):
    """Encola un evento COMPRA_REGISTRADA."""
    return _crear_evento(
        tipo='COMPRA_REGISTRADA',
        serializar=lambda: serializers.serializar_compra(compra),
        referencia=getattr(compra, 'numero_compra', '') or f'Compra-{compra.pk}',
        objeto_id_local=compra.pk,
        sucursal=compra.sucursal,
    )


def evento_inventario_movimiento(movimiento):
    """Encola un movimiento de inventario como ledger cloud."""
    return _crear_evento(
        tipo='INVENTARIO_MOVIMIENTO_REGISTRADO',
        serializar=lambda: serializers.serializar_movimiento_inventario(movimiento),
        referencia=f'MovInv-{movimiento.pk}-{movimiento.tipo}',
        objeto_id_local=movimiento.pk,
        sucursal=movimiento.lote.sucursal if movimiento.lote.sucursal_id else None,
    )


def evento_inventario_snapshot(sucursal=None):
    """
    Encola un snapshot completo del inventario local actual.

    UNICO evento que conserva el gate de SYNC_ENABLED, por dos razones:

    1. **Costo.** `serializar_inventario_snapshot` recorre todos los productos
       activos calculando FIFO por producto y mete el inventario completo en el
       payload. Sin cloud, acumular eso en cada venta llenaria la BD local de
       JSON que nadie va a leer.
    2. **Semantica.** Un snapshot es una foto de estado, no un hecho discreto:
       perder uno es inocuo porque el siguiente lo reemplaza.

    Regla general del outbox: los hechos de negocio se encolan siempre; las
    fotos de estado, solo si hay cloud a donde mandarlas.

    Por lo mismo se sigue emitiendo con `transaction.on_commit()` y NO dentro de
    la transaccion: no queremos alargar la transaccion de una venta con un
    recorrido O(N) del catalogo.
    """
    def _serializar():
        return serializers.serializar_inventario_snapshot(sucursal=sucursal)

    referencia = f'Snapshot-{sucursal.codigo if sucursal else "LOCAL"}'
    return _crear_evento(
        tipo='INVENTARIO_SNAPSHOT',
        serializar=_serializar,
        referencia=referencia,
        sucursal=sucursal,
        solo_con_cloud=True,
    )


def evento_cotizacion_creada(cotizacion):
    """Encola un evento COTIZACION_CREADA."""
    return _crear_evento(
        tipo='COTIZACION_CREADA',
        serializar=lambda: serializers.serializar_cotizacion(cotizacion),
        referencia=cotizacion.numero_cotizacion,
        objeto_id_local=cotizacion.pk,
        sucursal=cotizacion.sucursal,
    )


def evento_cotizacion_convertida(cotizacion):
    """Encola un evento COTIZACION_CONVERTIDA."""
    return _crear_evento(
        tipo='COTIZACION_CONVERTIDA',
        serializar=lambda: serializers.serializar_cotizacion(cotizacion),
        referencia=cotizacion.numero_cotizacion,
        objeto_id_local=cotizacion.pk,
        sucursal=cotizacion.sucursal,
    )


def evento_cxc_creada(cuenta):
    """Encola un evento CXC_CREADA."""
    return _crear_evento(
        tipo='CXC_CREADA',
        serializar=lambda: serializers.serializar_cxc(cuenta),
        referencia=cuenta.venta.numero_venta,
        objeto_id_local=cuenta.pk,
        sucursal=cuenta.sucursal,
    )


def evento_cxc_pago_registrado(pago):
    """Encola un evento CXC_PAGO_REGISTRADO."""
    return _crear_evento(
        tipo='CXC_PAGO_REGISTRADO',
        serializar=lambda: serializers.serializar_pago_cxc(pago),
        referencia=f'{pago.cuenta.venta.numero_venta}-P{pago.pk}',
        objeto_id_local=pago.pk,
        sucursal=pago.cuenta.sucursal,
    )


def evento_cxc_pago_anulado(pago):
    """Encola un evento CXC_PAGO_ANULADO."""
    return _crear_evento(
        tipo='CXC_PAGO_ANULADO',
        serializar=lambda: serializers.serializar_anulacion_pago_cxc(pago),
        referencia=f'{pago.cuenta.venta.numero_venta}-P{pago.pk}-ANUL',
        objeto_id_local=pago.pk,
        sucursal=pago.cuenta.sucursal,
    )


def evento_cxc_anulada(cuenta):
    """Encola un evento CXC_ANULADA."""
    return _crear_evento(
        tipo='CXC_ANULADA',
        serializar=lambda: serializers.serializar_cxc(cuenta),
        referencia=cuenta.venta.numero_venta,
        objeto_id_local=cuenta.pk,
        sucursal=cuenta.sucursal,
    )
