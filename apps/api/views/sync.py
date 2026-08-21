"""
apps/api/views/sync.py

Endpoints del cloud que reciben eventos desde las sucursales.

Este archivo vive en la MISMA base de codigo que el POS, pero solo tiene
sentido ejecutarlo en la instancia CLOUD (Django corriendo con settings que
apuntan a la BD cloud).

Handlers por tipo de evento (Opcion 3 del diseno, Fase 4.5):

    VENTA_CREADA       -> Crea Venta + DetalleVenta + Pago
    VENTA_ANULADA      -> Actualiza Venta existente con datos de anulacion
    APERTURA_CAJA      -> Crea TurnoCaja con estado='ABIERTO'
    MOVIMIENTO_CAJA    -> Crea MovimientoCaja colgado de turno ABIERTO
    CIERRE_CAJA        -> Actualiza TurnoCaja existente (abierto por APERTURA)
    AJUSTE_INVENTARIO  -> Ledger cloud de ajuste de inventario
    COMPRA_REGISTRADA  -> Ledger cloud de lineas de compra
    INVENTARIO_MOVIMIENTO_REGISTRADO -> Ledger cloud de movimiento de lote
    INVENTARIO_SNAPSHOT -> Upsert de stock actual por sucursal/SKU
    COTIZACION_CREADA  -> Crea cotizacion + detalles
    COTIZACION_CONVERTIDA -> Marca cotizacion convertida y vincula venta

Idempotencia:
    El hash_payload identifica eventos unicos. El cloud reutiliza la tabla
    EventoSync (la misma del cliente) para marcar que ya vio ese hash.
    Si el cliente reenvia por ACK perdido, el cloud responde DUPLICADO sin
    reprocesar.

Assert de integridad:
    Al cargarse este modulo, valida que todos los TIPOS_EVENTO_CODIGOS tengan
    handler definido. Si alguien agrega un tipo en constants.py sin agregar
    handler aqui, Django no arranca (error loud, no silent).
"""
import logging
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response

from apps.sync.constants import TIPOS_EVENTO_CODIGOS
from ..permissions import EsSucursalAutenticada
from ..serializers.sync import EventoBatchSerializer

logger = logging.getLogger('pos_system')


# ============================================================================
# POST /api/v1/sync/eventos/
# ============================================================================

@api_view(['POST'])
@permission_classes([EsSucursalAutenticada])
@throttle_classes([])
def recibir_eventos(request):
    """Recibe un batch de eventos desde una sucursal y los aplica."""
    recibir_eventos.throttle_scope = 'sync'

    serializer = EventoBatchSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {'error': 'Datos invalidos', 'detalle': serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    from apps.sync.models import EventoSync

    sucursal = getattr(request.auth, 'sucursal', None) if request.auth else None

    eventos = serializer.validated_data['eventos']
    resultados = []
    recibidos = 0
    duplicados = 0
    errores = 0

    for evento_data in eventos:
        hash_payload = evento_data['hash_payload']
        tipo_evento = evento_data['tipo_evento']
        payload = evento_data['payload']

        # Idempotencia, primer filtro: barato y cubre el caso normal (reenvio
        # posterior). NO es suficiente por si solo — dos requests concurrentes
        # pasan los dos por aca. El respaldo real es la constraint unica sobre
        # `hash_payload`, que se ejerce en el INSERT de mas abajo.
        if EventoSync.objects.filter(hash_payload=hash_payload).exists():
            duplicados += 1
            resultados.append({'hash': hash_payload, 'estado': 'DUPLICADO'})
            continue

        handler = HANDLERS.get(tipo_evento)
        if handler is None:
            errores += 1
            resultados.append({
                'hash': hash_payload,
                'estado': 'ERROR',
                'error': f'Tipo desconocido: {tipo_evento}',
            })
            continue

        try:
            with transaction.atomic():
                handler(sucursal, payload)
                # Este INSERT es la RESERVA del hecho, no solo su bitacora:
                # corre en la misma transaccion que el handler, asi que si otro
                # request concurrente ya reservo el hash, falla aca y revierte
                # tambien el efecto del handler. Sin esto, dos daemons
                # solapados duplicaban pagos CxC y movimientos de caja.
                EventoSync.objects.create(
                    sucursal=sucursal,
                    tipo_evento=tipo_evento,
                    payload=payload,
                    hash_payload=hash_payload,
                    objeto_referencia=_extraer_referencia(tipo_evento, payload),
                    estado='CONFIRMADO',
                    sent_at=timezone.now(),
                    confirmed_at=timezone.now(),
                )
            recibidos += 1
            resultados.append({'hash': hash_payload, 'estado': 'CONFIRMADO'})
            logger.info(
                '[SYNC] %s %s aplicado (hash=%s)',
                tipo_evento,
                _extraer_referencia(tipo_evento, payload),
                hash_payload[:12],
            )
        except IntegrityError:
            # Perdio la carrera contra otro request con el mismo hash. El otro
            # aplico el efecto y esta transaccion ya revirtio la duplicada:
            # para la sucursal el hecho esta entregado.
            duplicados += 1
            resultados.append({'hash': hash_payload, 'estado': 'DUPLICADO'})
            logger.info(
                '[SYNC] %s hash=%s aplicado por otra request concurrente',
                tipo_evento, hash_payload[:12],
            )
        except Exception as exc:
            errores += 1
            resultados.append({
                'hash': hash_payload,
                'estado': 'ERROR',
                'error': str(exc)[:500],
            })
            logger.exception(
                '[SYNC] Error aplicando %s hash=%s: %s',
                tipo_evento, hash_payload[:12], exc,
            )

    if sucursal and hasattr(sucursal, 'ultima_sync'):
        try:
            sucursal.ultima_sync = timezone.now()
            sucursal.save(update_fields=['ultima_sync'])
        except Exception:
            pass

    return Response({
        'recibidos': recibidos,
        'duplicados': duplicados,
        'errores': errores,
        'detalle': resultados,
        'timestamp': timezone.now(),
    }, status=status.HTTP_200_OK)


# ============================================================================
# GET /api/v1/sync/status/
# ============================================================================

@api_view(['GET'])
@permission_classes([EsSucursalAutenticada])
def sync_status(request):
    """Estado de sincronizacion desde el punto de vista del cloud."""
    from apps.sync.models import EventoSync, VersionMaestro

    sucursal = getattr(request.auth, 'sucursal', None) if request.auth else None
    if sucursal is None:
        return Response(
            {'error': 'Token sin sucursal asociada'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    qs = EventoSync.objects.filter(sucursal=sucursal)
    return Response({
        'sucursal_codigo': sucursal.codigo,
        'eventos_pendientes': qs.filter(estado='PENDIENTE').count(),
        'eventos_confirmados': qs.filter(estado='CONFIRMADO').count(),
        'eventos_error': qs.filter(estado='ERROR').count(),
        'ultima_sync': qs.filter(estado='CONFIRMADO')
            .order_by('-confirmed_at')
            .values_list('confirmed_at', flat=True)
            .first(),
        'version_maestros': {
            vm.tabla: vm.ultima_version
            for vm in VersionMaestro.objects.all()
        },
    })


# ============================================================================
# POST /api/v1/sync/heartbeat/
# ============================================================================

@api_view(['POST'])
@permission_classes([EsSucursalAutenticada])
@throttle_classes([])
def heartbeat(request):
    """Actualiza liveness de la sucursal aunque no haya eventos pendientes."""
    sucursal = getattr(request.auth, 'sucursal', None) if request.auth else None
    if sucursal is None:
        return Response(
            {'error': 'Token sin sucursal asociada'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    sucursal.ultima_sync = timezone.now()
    sucursal.save(update_fields=['ultima_sync'])
    return Response({
        'sucursal_codigo': sucursal.codigo,
        'ultima_sync': sucursal.ultima_sync,
    })


# ============================================================================
# GET /api/v1/sync/roles/  — definiciones de rol del negocio (cloud -> sucursal)
# ============================================================================

def _filtrar_keyset_sync(qs, request):
    """
    Aplica el cursor keyset (?desde= + ?desde_id=) a un queryset de sync.

    Comparte semantica con `SyncIncrementalMixin` de apps/api/views/maestros.py:
    el corte es sobre la tupla `(fecha_modificacion, id)`, no solo sobre la
    fecha. Sin el desempate, dos registros guardados en el mismo instante hacen
    que el segundo se pierda cuando el cursor queda parado en ese valor.

    Estos endpoints NO paginan, asi que la inestabilidad de paginacion no les
    aplica; el empate de timestamps si.
    """
    desde = request.query_params.get('desde')
    if not desde:
        return qs

    ts = parse_datetime(desde)
    if not ts:
        return qs

    desde_id = request.query_params.get('desde_id')
    try:
        desde_id = int(desde_id) if desde_id else None
    except (TypeError, ValueError):
        desde_id = None

    if desde_id is None:
        return qs.filter(fecha_modificacion__gt=ts)

    return qs.filter(
        Q(fecha_modificacion__gt=ts)
        | Q(fecha_modificacion=ts, id__gt=desde_id)
    )


@api_view(['GET'])
@permission_classes([EsSucursalAutenticada])
def roles_para_sucursal(request):
    """
    Devuelve las definiciones de rol (rol -> permisos) del negocio de la sucursal
    autenticada, para que la sucursal las sincronice localmente. Solo lectura,
    scoped al negocio del token. Filtro incremental ?desde=<ISO>.

    La asignacion usuario->rol NO se sincroniza (es local). Esto propaga solo
    "que puede cada rol" configurado en el portal.
    """
    from django.utils.dateparse import parse_datetime
    from apps.permisos.models import Rol

    sucursal = getattr(request.auth, 'sucursal', None) if request.auth else None
    negocio_id = getattr(sucursal, 'negocio_id', None) if sucursal else None
    if not negocio_id:
        return Response([])

    qs = (Rol.objects.filter(negocio_id=negocio_id)
          .prefetch_related('permisos')
          .order_by('fecha_modificacion', 'id'))
    qs = _filtrar_keyset_sync(qs, request)

    data = [
        {
            'slug': r.slug,
            'nombre': r.nombre,
            'activo': r.activo,
            'permisos': sorted(p.codigo for p in r.permisos.all()),
            'fecha_modificacion': r.fecha_modificacion.isoformat(),
            # Token de paginacion, NO identidad. La identidad cross-BD de un rol
            # es su `slug`; esto solo desempata el cursor.
            'cursor_id': r.id,
        }
        for r in qs
    ]
    return Response(data)


# ============================================================================
# GET /api/v1/sync/asignaciones/  — usuario -> rol (cloud -> sucursal)
# ============================================================================

@api_view(['GET'])
@permission_classes([EsSucursalAutenticada])
def asignaciones_para_sucursal(request):
    """
    Devuelve las asignaciones usuario->rol relevantes para la sucursal
    autenticada: globales del negocio (sucursal NULL) + las acotadas a esa
    sucursal. Usa identidades naturales cross-DB:
      - usuario_username
      - rol_slug
      - sucursal_codigo
    """
    from django.db.models import Q
    from django.utils.dateparse import parse_datetime
    from apps.permisos.models import AsignacionRol

    sucursal = getattr(request.auth, 'sucursal', None) if request.auth else None
    negocio_id = getattr(sucursal, 'negocio_id', None) if sucursal else None
    if not negocio_id:
        return Response([])

    qs = (
        AsignacionRol.objects
        .filter(rol__negocio_id=negocio_id)
        .filter(Q(sucursal__isnull=True) | Q(sucursal=sucursal))
        .select_related('usuario', 'rol', 'sucursal')
        .order_by('fecha_modificacion', 'id')
    )
    qs = _filtrar_keyset_sync(qs, request)

    data = [
        {
            'usuario_username': a.usuario.username,
            'rol_slug': a.rol.slug,
            'sucursal_codigo': a.sucursal.codigo if a.sucursal_id else None,
            'activo': a.activo,
            'fecha_modificacion': a.fecha_modificacion.isoformat(),
            # Token de paginacion, NO identidad. La identidad cross-BD de una
            # asignacion son sus claves naturales (usuario_username + rol_slug +
            # sucursal_codigo); esto solo desempata el cursor.
            'cursor_id': a.id,
        }
        for a in qs
    ]
    return Response(data)


# ============================================================================
# GET /api/v1/sync/metodos-credito/
# ============================================================================

@api_view(['GET'])
@permission_classes([EsSucursalAutenticada])
def metodos_credito_para_sucursal(request):
    """Devuelve reglas de credito globales o especificas de la sucursal."""
    from django.db.models import Q
    from django.utils.dateparse import parse_datetime
    from apps.cuentas_por_cobrar.models import MetodoPlazoCredito

    sucursal = getattr(request.auth, 'sucursal', None) if request.auth else None
    if sucursal is None:
        return Response([])

    qs = (
        MetodoPlazoCredito.objects
        .filter(Q(sucursal__isnull=True) | Q(sucursal=sucursal))
        .select_related('sucursal')
        .order_by('fecha_modificacion', 'id')
    )
    qs = _filtrar_keyset_sync(qs, request)

    return Response([
        {
            'nombre': m.nombre,
            'tipo': m.tipo,
            'dias_vencimiento': m.dias_vencimiento,
            'cantidad_cuotas': m.cantidad_cuotas,
            'frecuencia': m.frecuencia,
            'inicial_minima_porcentaje': str(m.inicial_minima_porcentaje),
            'interes_porcentaje': str(m.interes_porcentaje),
            'activo': m.activo,
            'sucursal_codigo': m.sucursal.codigo if m.sucursal_id else None,
            'fecha_modificacion': m.fecha_modificacion.isoformat(),
            # Token de paginacion, NO identidad (la identidad es `nombre`).
            'cursor_id': m.id,
        }
        for m in qs
    ])


# ============================================================================
# GET /api/v1/sync/configuracion/
# ============================================================================

@api_view(['GET'])
@permission_classes([EsSucursalAutenticada])
def configuracion_para_sucursal(request):
    """Devuelve solo configuracion cloud-safe; excluye hardware/local."""
    from apps.configuracion.models import ConfiguracionNegocio

    sucursal = getattr(request.auth, 'sucursal', None) if request.auth else None
    if sucursal is None:
        return Response([])

    config = ConfiguracionNegocio.load(sucursal=sucursal)

    # Sync incremental: si la sucursal ya tiene una version igual o mas
    # reciente, no devolvemos nada (evita reescribir la config local en cada
    # ciclo). Mismo contrato ?desde= que el resto de endpoints de sync.
    desde = request.query_params.get('desde')
    if desde:
        ts = parse_datetime(desde)
        if ts and config.fecha_modificacion and config.fecha_modificacion <= ts:
            return Response([])

    data = {
        'nombre_negocio': config.nombre_negocio,
        'rnc': config.rnc,
        'direccion': config.direccion,
        'telefono': config.telefono,
        'email_negocio': config.email_negocio,
        'permitir_inventario_negativo': config.permitir_inventario_negativo,
        'modulo_etiquetas_zebra': config.modulo_etiquetas_zebra,
        'modulo_financiacion_coop': config.modulo_financiacion_coop,
        'modulo_cotizaciones': config.modulo_cotizaciones,
        'modulo_impresion_termica': config.modulo_impresion_termica,
        'modulo_barcode_scanner': config.modulo_barcode_scanner,
        'modulo_reportes_ondemand': config.modulo_reportes_ondemand,
        'modulo_ecf': config.modulo_ecf,
        'modulo_dashboard': config.modulo_dashboard,
        'pago_efectivo': config.pago_efectivo,
        'pago_transferencia': config.pago_transferencia,
        'pago_tarjeta': config.pago_tarjeta,
        'formato_codigo_barras': config.formato_codigo_barras,
        'dias_anulacion': config.dias_anulacion,
        'cantidad_copias_ticket': getattr(config, 'cantidad_copias_ticket', 1),
        'ecf_proveedor': config.ecf_proveedor,
        'itbis_incluido_en_precio': config.itbis_incluido_en_precio,
        'itbis_porcentaje_global': str(config.itbis_porcentaje_global),
        'modo_contingencia': config.modo_contingencia,
        'fecha_modificacion': config.fecha_modificacion.isoformat(),
    }
    return Response([data])


# ============================================================================
# HANDLERS por tipo de evento
# ============================================================================

def _extraer_referencia(tipo_evento, payload):
    """Extrae una referencia legible del payload para logs/admin."""
    if tipo_evento in ('VENTA_CREADA', 'VENTA_ANULADA'):
        return payload.get('numero_venta', '')
    if tipo_evento in ('APERTURA_CAJA', 'CIERRE_CAJA'):
        return f"Turno-{payload.get('turno_id_local', '?')}"
    if tipo_evento == 'MOVIMIENTO_CAJA':
        return f"Mov-{payload.get('movimiento_id_local', '?')}-{payload.get('tipo', '?')}"
    if tipo_evento == 'AJUSTE_INVENTARIO':
        return f"Ajuste-{payload.get('ajuste_id_local', '?')}"
    if tipo_evento == 'COMPRA_REGISTRADA':
        return payload.get('numero_compra', '')
    if tipo_evento == 'INVENTARIO_MOVIMIENTO_REGISTRADO':
        return f"MovInv-{payload.get('movimiento_id_local', '?')}-{payload.get('tipo', '?')}"
    if tipo_evento == 'INVENTARIO_SNAPSHOT':
        return f"Snapshot-{payload.get('sucursal_codigo') or '?'}"
    if tipo_evento in ('COTIZACION_CREADA', 'COTIZACION_CONVERTIDA'):
        return payload.get('numero_cotizacion', '')
    if tipo_evento in ('CXC_CREADA', 'CXC_ANULADA'):
        return payload.get('numero_venta', '')
    if tipo_evento == 'CXC_PAGO_REGISTRADO':
        return f"{payload.get('numero_venta', '')}-P{payload.get('pago_id_local', '?')}"
    return ''


def _buscar_turno_abierto(sucursal, caja_nombre, fecha_apertura, origen_id=None):
    """
    Busca el TurnoCaja ABIERTO correspondiente a una sucursal+caja+apertura.

    Estrategia:
    1. Busca por caja+fecha_apertura exacta (lo mas preciso)
    2. Si no existe, busca el ultimo abierto de esa caja como fallback

    Retorna None si no existe. El caller decide que hacer.
    """
    from apps.caja.models import TurnoCaja

    try:
        caja = _obtener_caja(sucursal, caja_nombre, origen_id)
    except Exception:
        return None

    fecha = parse_datetime(fecha_apertura) if fecha_apertura else None

    if fecha:
        # `estado='ABIERTO'` TAMBIEN en esta rama. La funcion promete devolver
        # un turno abierto, pero la busqueda por caja+fecha no filtraba estado:
        # devolvia un turno YA CERRADO antes de llegar al fallback, y el
        # movimiento se colgaba de el sin recalcular el cierre. El detalle cloud
        # terminaba con un gasto que nunca afecto al esperado ni a la
        # diferencia.
        turno = TurnoCaja.objects.filter(
            caja=caja,
            fecha_apertura=fecha,
            estado='ABIERTO',
        ).first()
        if turno:
            return turno

    # Fallback: ultimo turno abierto de la caja
    return TurnoCaja.objects.filter(caja=caja, estado='ABIERTO').first()


def _obtener_caja(sucursal, caja_nombre, origen_id=None):
    """
    Resuelve la caja de una sucursal.

    Prioridad:
      1. `origen_id` -> identidad estable, sobrevive a renombres.
      2. Nombre -> bootstrap y compatibilidad con sucursales que todavia no
         envian la identidad.

    Antes solo existia (2), y `nombre` es mutable: renombrar una caja entre la
    apertura y el cierre hacia que el movimiento no encontrara su turno y que
    el cierre creara otro turno bajo una caja nueva.
    """
    from apps.caja.models import Caja

    if origen_id:
        caja = Caja.objects.filter(origen_id=origen_id, sucursal=sucursal).first()
        if caja is not None:
            # El nombre es un atributo mas: se actualiza si cambio en origen.
            if caja_nombre and caja.nombre != caja_nombre:
                caja.nombre = caja_nombre
                caja.save(update_fields=['nombre'])
            return caja

    caja, creada = Caja.objects.get_or_create(
        nombre=caja_nombre,
        sucursal=sucursal,
        defaults={'activa': True},
    )
    # Primera vez que llega la identidad: se sella sobre la caja ya existente.
    if origen_id and not creada:
        Caja.objects.filter(pk=caja.pk).update(origen_id=origen_id)
        caja.refresh_from_db(fields=['origen_id'])
    elif origen_id and creada:
        Caja.objects.filter(pk=caja.pk).update(origen_id=origen_id)
        caja.refresh_from_db(fields=['origen_id'])
    return caja


def _resolver_usuario(username):
    """Resuelve username -> User o None."""
    if not username:
        return None
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return User.objects.filter(username=username).first()


def _resolver_o_crear_cliente(sucursal, payload, crear=True):
    """
    Resuelve el cliente de un evento y, si hace falta, lo crea.

    Resolutor UNICO para ventas, cuentas por cobrar y cotizaciones. Antes cada
    handler lo hacia por su cuenta y con criterios distintos.

    Motivacion (BUG-C en docs/BUGS.md): la resolucion era solo por `cedula_rnc`,
    que es un campo opcional y en la practica vacio. Consecuencia real medida en
    produccion: 404 de 405 ventas quedaron sin cliente y las 16 ventas a credito
    de Royal Plast nunca pudieron replicar su cuenta por cobrar
    (RD$240,435 invisibles en el portal).

    Orden de resolucion:
        1. `cedula_rnc`, si viene con valor. Sigue mandando: es la identidad
           real del negocio cuando existe.
        2. `(origen_sucursal, origen_id_local)`. Identidad estable que no
           depende de datos que el negocio puede omitir.
        3. Crear el cliente con lo que trae el payload, sellando su origen.

    Esto revisa a proposito la decision B11b de docs/ROADMAP_PORTAL.md ("el
    cloud es el unico autor de clientes"). Un cliente puede nacer en la sucursal
    y promoverse al cloud; queda marcado con su origen para que el portal pueda
    revisarlo. El cloud sigue siendo la autoridad para EDITAR: aqui solo se crea
    lo que no existe.

    Devuelve None si no hay forma de identificar ni datos para crear.
    """
    from apps.clientes.models import Cliente

    datos = payload.get('cliente') or {}
    cedula = datos.get('cedula_rnc') or payload.get('cliente_cedula_rnc')
    id_local = datos.get('id_local')

    # 1) Por cedula/RNC.
    if cedula:
        cliente = Cliente.objects.filter(cedula_rnc=cedula).first()
        if cliente:
            return cliente

    # 2) Por origen (sucursal + PK local).
    if sucursal is not None and id_local:
        cliente = Cliente.objects.filter(
            origen_sucursal=sucursal,
            origen_id_local=id_local,
        ).first()
        if cliente:
            # Backfill acotado: si el cliente nacio sin cedula y la sucursal ya
            # se la cargo, la subimos. Sin esto el pull de maestros devolveria
            # la cedula vacia a la sucursal y borraria lo que el cajero tecleo.
            if cedula and not cliente.cedula_rnc:
                cliente.cedula_rnc = cedula
                cliente.save(update_fields=['cedula_rnc', 'fecha_modificacion'])
            return cliente

    # 3) Crear. Sin datos del cliente no hay nada que crear (payload viejo).
    if not crear or not datos or not datos.get('nombre'):
        return None

    tipo = datos.get('tipo')
    if tipo not in dict(Cliente.TIPOS):
        tipo = 'PERSONAL'

    cliente = Cliente.objects.create(
        tipo=tipo,
        nombre=datos['nombre'],
        cedula_rnc=cedula or None,
        telefono=datos.get('telefono') or '',
        direccion=datos.get('direccion') or '',
        limite_credito=Decimal(str(datos.get('limite_credito') or '0')),
        plazo_credito_dias=int(datos.get('plazo_credito_dias') or 30),
        origen_sucursal=sucursal,
        origen_id_local=id_local,
    )
    logger.info(
        'Cliente %s creado en cloud desde evento de sucursal %s (id_local=%s)',
        cliente.nombre, getattr(sucursal, 'codigo', None), id_local,
    )
    return cliente


# ---- VENTAS ----

def _handler_venta_creada(sucursal, payload):
    """Crea Venta + DetalleVenta + Pago a partir del payload."""
    from apps.ventas.models import Venta, DetalleVenta, Pago
    from apps.productos.models import Producto
    from apps.clientes.models import Cliente

    numero_venta = payload.get('numero_venta')
    if not numero_venta:
        raise ValueError('Payload sin numero_venta')

    existente = Venta.objects.filter(numero_venta=numero_venta).first()
    if existente:
        # Reenvio CORRECTIVO, no un no-op. Una venta replicada antes pudo quedar
        # sin cliente (el payload no traia forma de identificarlo) o con lineas
        # omitidas (un SKU que todavia no existia en cloud). Si ahora se puede
        # completar, se completa. Nunca se pisa un cliente ya asignado.
        if existente.cliente_id is None:
            cliente = _resolver_o_crear_cliente(sucursal, payload)
            if cliente is not None:
                existente.cliente = cliente
                existente.save(update_fields=['cliente'])
                logger.info('Venta %s: cliente enlazado en reenvio', numero_venta)

        _reparar_lineas_venta(existente, payload, numero_venta)
        return

    # Todas las dependencias ANTES de crear nada. Antes, un SKU que todavia no
    # existia en cloud solo generaba un warning y esa linea se omitia: la venta
    # quedaba con cabecera y pagos completos pero detalles incompletos, y el
    # evento se confirmaba, asi que ese reenvio ya no podia repararla nunca.
    # Fallar el evento entero lo deja reintentable: cuando el producto llegue,
    # el mismo evento se aplica completo. Es el criterio que ya usaba el
    # handler de cotizaciones.
    productos_por_sku = _resolver_productos_venta(payload, numero_venta)

    # Venta.usuario es NOT NULL: si el cajero no existe en cloud (username no
    # resuelto), caer al usuario de servicio de la sucursal en vez de reventar
    # con IntegrityError y dejar el evento en ERROR (la venta nunca se replicaria).
    # Mismo patron que el handler de pagos CxC (cae a cuenta.creado_por).
    usuario = _resolver_usuario(payload.get('usuario_username')) or sucursal.usuario_servicio
    cliente = _resolver_o_crear_cliente(sucursal, payload)

    venta = Venta.objects.create(
        numero_venta=numero_venta,
        sucursal=sucursal,
        usuario=usuario,
        cliente=cliente,
        subtotal=Decimal(payload.get('subtotal', '0')),
        descuento_total=Decimal(payload.get('descuento_total', '0')),
        total=Decimal(payload.get('total', '0')),
        estado=payload.get('estado', 'COMPLETADA'),
        condicion_pago=payload.get('condicion_pago', 'CONTADO'),
        notas=payload.get('notas', '') or '',
        # Sucursal con codigo viejo no manda estas claves: quedan en None/''.
        descuento_autorizado_por=_resolver_usuario(
            payload.get('descuento_autorizado_por')
        ),
        descuento_autorizacion_motivo=payload.get('descuento_autorizacion_motivo', '') or '',
    )

    fecha = parse_datetime(payload['fecha_venta']) if payload.get('fecha_venta') else None
    if fecha:
        Venta.objects.filter(pk=venta.pk).update(fecha_venta=fecha)

    for d in payload.get('detalles', []):
        producto = productos_por_sku[d.get('producto_sku')]
        DetalleVenta.objects.create(
            venta=venta,
            producto=producto,
            cantidad=Decimal(d.get('cantidad', '0')),
            precio_unitario=Decimal(d.get('precio_unitario', '0')),
            subtotal=Decimal(d.get('subtotal', '0')),
            descuento_monto=Decimal(d.get('descuento_monto', '0')),
            descuento_porcentaje=Decimal(d.get('descuento_porcentaje', '0')),
            total_linea=Decimal(d.get('total_linea', '0')),
            costo_fifo=Decimal(d.get('costo_fifo', '0')),
        )

    for p in payload.get('pagos', []):
        Pago.objects.create(
            venta=venta,
            metodo=p.get('metodo', 'EFECTIVO'),
            monto=Decimal(p.get('monto', '0')),
            referencia=p.get('referencia', '') or '',
        )


def _resolver_productos_venta(payload, numero_venta):
    """
    Mapa {sku: Producto} con TODOS los productos que la venta necesita.

    Levanta si falta alguno: una venta a medias no es una venta. El evento
    queda reintentable y se aplica completo cuando el producto llegue al cloud.
    """
    from apps.productos.models import Producto

    skus = [d.get('producto_sku') for d in payload.get('detalles', [])]
    encontrados = {
        p.sku: p
        for p in Producto.objects.filter(sku__in=[s for s in skus if s])
    }

    faltantes = sorted({s for s in skus if s not in encontrados})
    if faltantes:
        raise ValueError(
            f'Venta {numero_venta}: los productos {faltantes} no existen en '
            f'cloud todavia. El evento se reintenta cuando lleguen.'
        )

    return encontrados


def _reparar_lineas_venta(venta, payload, numero_venta):
    """
    Completa detalles y pagos de una venta replicada de forma incompleta.

    Solo actua cuando lo persistido no cuadra con el payload; si cuadra, no
    toca nada. Repara las ventas que quedaron partidas ANTES de que el handler
    validara dependencias por adelantado.
    """
    from apps.ventas.models import DetalleVenta, Pago

    detalles_payload = payload.get('detalles', [])
    pagos_payload = payload.get('pagos', [])

    if venta.detalles.count() != len(detalles_payload):
        productos_por_sku = _resolver_productos_venta(payload, numero_venta)
        venta.detalles.all().delete()
        for d in detalles_payload:
            DetalleVenta.objects.create(
                venta=venta,
                producto=productos_por_sku[d.get('producto_sku')],
                cantidad=Decimal(d.get('cantidad', '0')),
                precio_unitario=Decimal(d.get('precio_unitario', '0')),
                subtotal=Decimal(d.get('subtotal', '0')),
                descuento_monto=Decimal(d.get('descuento_monto', '0')),
                descuento_porcentaje=Decimal(d.get('descuento_porcentaje', '0')),
                total_linea=Decimal(d.get('total_linea', '0')),
                costo_fifo=Decimal(d.get('costo_fifo', '0')),
            )
        logger.info(
            'Venta %s: %d linea(s) reconstruidas en reenvio correctivo',
            numero_venta, len(detalles_payload),
        )

    if venta.pagos.count() != len(pagos_payload):
        venta.pagos.all().delete()
        for p in pagos_payload:
            Pago.objects.create(
                venta=venta,
                metodo=p.get('metodo', 'EFECTIVO'),
                monto=Decimal(p.get('monto', '0')),
                referencia=p.get('referencia', '') or '',
            )
        logger.info(
            'Venta %s: %d pago(s) reconstruidos en reenvio correctivo',
            numero_venta, len(pagos_payload),
        )


def _handler_venta_anulada(sucursal, payload):
    """Marca una venta existente como anulada."""
    from apps.ventas.models import Venta

    numero = payload.get('numero_venta')
    if not numero:
        raise ValueError('Payload sin numero_venta')

    try:
        venta = Venta.objects.get(numero_venta=numero)
    except Venta.DoesNotExist:
        raise ValueError(f'Venta {numero} no existe en cloud (posiblemente llegara pronto)')

    anulada_por = _resolver_usuario(payload.get('anulada_por_username'))
    fecha_anul = parse_datetime(payload['fecha_anulacion']) if payload.get('fecha_anulacion') else None

    venta.estado = payload.get('estado', 'ANULADA')
    venta.fecha_anulacion = fecha_anul
    venta.anulada_por = anulada_por
    venta.motivo_anulacion = payload.get('motivo_anulacion', '') or ''
    venta.save(update_fields=[
        'estado', 'fecha_anulacion', 'anulada_por', 'motivo_anulacion',
    ])


# ---- CAJA ----

def _handler_apertura_caja(sucursal, payload):
    """
    Crea un TurnoCaja con estado='ABIERTO'.

    En Opcion 3, este es el evento que CREA el turno en el cloud. Los
    movimientos (MOVIMIENTO_CAJA) y el cierre (CIERRE_CAJA) se cuelgan
    de este turno despues.
    """
    from apps.caja.models import TurnoCaja

    caja = _obtener_caja(
        sucursal,
        payload.get('caja_nombre', 'Caja Principal'),
        payload.get('caja_origen_id'),
    )
    usuario = _resolver_usuario(payload.get('usuario_username'))
    fecha_apertura = parse_datetime(payload['fecha_apertura']) if payload.get('fecha_apertura') else timezone.now()

    # Idempotencia secundaria: si ya existe turno (abierto o cerrado) con la
    # misma caja+fecha_apertura, no crear duplicado
    existente = TurnoCaja.objects.filter(
        caja=caja,
        fecha_apertura=fecha_apertura,
    ).first()
    if existente:
        logger.info(
            'Turno ya existe en cloud (caja=%s apertura=%s estado=%s), skip',
            caja.nombre, fecha_apertura, existente.estado,
        )
        return

    TurnoCaja.objects.create(
        caja=caja,
        usuario=usuario,
        estado='ABIERTO',
        fecha_apertura=fecha_apertura,
        fondo_apertura=Decimal(payload.get('fondo_apertura', '0')),
        notas_apertura=payload.get('notas_apertura', '') or '',
    )


def _handler_movimiento_caja(sucursal, payload):
    """
    Crea un MovimientoCaja colgado del turno abierto correspondiente.

    Busca el turno por (sucursal, caja, fecha_apertura). Si no lo encuentra
    (caso raro: el evento MOVIMIENTO llego antes que APERTURA por reintentos),
    lanza ValueError para que el cliente reintente.
    """
    from apps.caja.models import MovimientoCaja

    turno = _buscar_turno_abierto(
        sucursal,
        payload.get('caja_nombre', 'Caja Principal'),
        payload.get('turno_fecha_apertura'),
        payload.get('caja_origen_id'),
    )
    if not turno:
        raise ValueError(
            'Turno abierto no existe en cloud todavia (apertura llegara pronto)'
        )

    # Idempotencia secundaria: buscar movimiento por (turno, fecha, tipo, monto)
    fecha = parse_datetime(payload['fecha']) if payload.get('fecha') else timezone.now()
    existente = MovimientoCaja.objects.filter(
        turno=turno,
        fecha=fecha,
        tipo=payload.get('tipo'),
        monto=Decimal(payload.get('monto', '0')),
    ).exists()
    if existente:
        logger.info('Movimiento ya existe en cloud, skip')
        return

    registrado_por = _resolver_usuario(payload.get('registrado_por_username'))
    autorizado_por = _resolver_usuario(payload.get('autorizado_por_username'))

    if not registrado_por:
        raise ValueError(
            f"Usuario {payload.get('registrado_por_username')} no existe en cloud"
        )

    MovimientoCaja.objects.create(
        turno=turno,
        tipo=payload.get('tipo'),
        monto=Decimal(payload.get('monto', '0')),
        descripcion=payload.get('descripcion', '') or '',
        registrado_por=registrado_por,
        autorizado_por=autorizado_por,
        fecha=fecha,
    )


def _handler_cierre_caja(sucursal, payload):
    """
    Cierra el TurnoCaja existente (creado por APERTURA_CAJA).

    Fallback: si el turno no existe (caso raro, APERTURA nunca llego), lo
    crea en estado CERRADO con los datos disponibles. Asi el cierre no
    se pierde aunque la apertura se haya perdido.
    """
    from apps.caja.models import TurnoCaja

    fecha_apertura = parse_datetime(payload['fecha_apertura']) if payload.get('fecha_apertura') else None
    fecha_cierre = parse_datetime(payload['fecha_cierre']) if payload.get('fecha_cierre') else timezone.now()

    caja = _obtener_caja(
        sucursal,
        payload.get('caja_nombre', 'Caja Principal'),
        payload.get('caja_origen_id'),
    )

    # Buscar el turno creado por APERTURA_CAJA
    turno = None
    if fecha_apertura:
        turno = TurnoCaja.objects.filter(
            caja=caja,
            fecha_apertura=fecha_apertura,
        ).first()

    usuario = _resolver_usuario(payload.get('usuario_username'))
    cerrado_por = _resolver_usuario(payload.get('cerrado_por_username'))

    monto_contado = Decimal(payload['monto_contado']) if payload.get('monto_contado') else None
    monto_esperado = Decimal(payload['monto_esperado']) if payload.get('monto_esperado') else None
    diferencia = Decimal(payload['diferencia']) if payload.get('diferencia') else None

    if turno:
        # Path feliz: actualizar el turno abierto
        if turno.estado == 'CERRADO':
            logger.info('Turno %s ya esta CERRADO en cloud, skip', turno.pk)
            return

        turno.estado = 'CERRADO'
        turno.fecha_cierre = fecha_cierre
        turno.monto_contado = monto_contado
        turno.monto_esperado = monto_esperado
        turno.diferencia = diferencia
        turno.cerrado_por = cerrado_por
        turno.notas_cierre = payload.get('notas_cierre', '') or ''
        turno.save()
    else:
        # Fallback: APERTURA nunca llego, crear turno ya cerrado
        logger.warning(
            'Cierre recibido sin apertura previa (caja=%s apertura=%s). '
            'Creando turno CERRADO directamente.',
            caja.nombre, fecha_apertura,
        )
        TurnoCaja.objects.create(
            caja=caja,
            usuario=usuario,
            estado='CERRADO',
            fecha_apertura=fecha_apertura or fecha_cierre,
            fecha_cierre=fecha_cierre,
            fondo_apertura=Decimal(payload.get('fondo_apertura', '0')),
            monto_contado=monto_contado,
            monto_esperado=monto_esperado,
            diferencia=diferencia,
            cerrado_por=cerrado_por,
            notas_cierre=payload.get('notas_cierre', '') or '',
        )


# ---- INVENTARIO / COMPRAS ----

def _handler_ajuste_inventario(sucursal, payload):
    """Registra un ajuste como movimiento auditable en el ledger cloud."""
    payload = {
        **payload,
        'movimiento_id_local': payload.get('ajuste_id_local'),
        'referencia_tipo': 'AjusteInventario',
        'referencia_id': payload.get('ajuste_id_local'),
        'fecha_movimiento': payload.get('fecha'),
        'notas': payload.get('motivo', ''),
    }
    _registrar_movimiento_inventario_sync(sucursal, payload)


def _handler_compra(sucursal, payload):
    """
    Registra la compra. NO escribe el ledger de inventario.

    Una compra viajaba al cloud por DOS caminos y ambos escribian ledger:

      - `COMPRA_REGISTRADA` creaba una fila por linea, con
        `movimiento_id_local=None` y deduplicacion por clave natural.
      - `INVENTARIO_MOVIMIENTO_REGISTRADO` creaba otra fila por el mismo hecho,
        con el ID real del `MovimientoLote`.

    Resultado: cada linea de compra quedaba DUPLICADA en el ledger cloud, y al
    corregir la compra se actualizaba solo la fila con ID — las dos versiones
    divergian y ninguna suma cuadraba.

    La autoridad ahora es UNA: los eventos de movimiento. Traen
    `movimiento_id_local`, que es identidad estable y ya esta protegida por la
    constraint `unique(sucursal, movimiento_id_local)`; permite update al
    corregir y no depende de una clave natural que puede cambiar.

    `_encolar_compra_y_movimientos` emite ambos eventos en la misma
    transaccion, asi que no se pierde nada al dejar de escribir aca.
    """
    logger.info(
        '[SYNC] Compra %s recibida; el ledger lo escriben sus eventos de '
        'movimiento (autoridad unica).',
        payload.get('numero_compra') or payload.get('compra_id_local'),
    )


def _handler_movimiento_inventario(sucursal, payload):
    """Registra un MovimientoLote replicado desde la sucursal."""
    _registrar_movimiento_inventario_sync(sucursal, payload)


def _handler_inventario_snapshot(sucursal, payload):
    """
    Upsert del stock actual por producto para una sucursal.

    El snapshot es last-write-wins, pero "last" se decide por el timestamp que
    trae el payload, NO por el orden de llegada. Un reintento o una corrida
    manual pueden entregar un snapshot viejo despues de uno nuevo; aplicarlo
    incondicionalmente hacia retroceder existencias y valuacion en el portal
    hasta que llegara otro snapshot.
    """
    from apps.sync.models import InventarioSucursalSnapshot

    timestamp = parse_datetime(payload['timestamp']) if payload.get('timestamp') else timezone.now()

    vigentes = {
        fila.producto_sku: fila.timestamp
        for fila in InventarioSucursalSnapshot.objects.filter(
            sucursal=sucursal,
            producto_sku__in=[
                item.get('producto_sku')
                for item in payload.get('items', [])
                if item.get('producto_sku')
            ],
        ).only('producto_sku', 'timestamp')
    }

    obsoletos = 0
    for item in payload.get('items', []):
        sku = item.get('producto_sku')
        if not sku:
            continue

        vigente = vigentes.get(sku)
        if vigente is not None and timestamp < vigente:
            obsoletos += 1
            continue

        InventarioSucursalSnapshot.objects.update_or_create(
            sucursal=sucursal,
            producto_sku=sku,
            defaults={
                'producto_nombre': item.get('producto_nombre', '') or '',
                'stock_actual': int(item.get('stock_actual') or 0),
                'stock_minimo': int(item.get('stock_minimo') or 0),
                'bajo_stock': bool(item.get('bajo_stock', False)),
                'valor_fifo': Decimal(str(item.get('valor_fifo') or '0')),
                'timestamp': timestamp,
                'payload': item,
            },
        )

    if obsoletos:
        logger.info(
            'Snapshot de %s con timestamp %s: %d SKU(s) omitidos por tener una '
            'foto mas reciente aplicada.',
            getattr(sucursal, 'codigo', None), timestamp, obsoletos,
        )


def _registrar_movimiento_inventario_sync(sucursal, payload):
    from apps.sync.models import InventarioMovimientoSync

    if sucursal is None:
        raise ValueError('Movimiento de inventario sin sucursal autenticada')

    sku = payload.get('producto_sku')
    if not sku:
        raise ValueError('Movimiento de inventario sin producto_sku')

    fecha = (
        parse_datetime(payload['fecha_movimiento'])
        if payload.get('fecha_movimiento') else timezone.now()
    )
    movimiento_id = payload.get('movimiento_id_local')
    defaults = {
        'tipo': payload.get('tipo') or 'AJUSTE',
        'referencia_tipo': payload.get('referencia_tipo', '') or '',
        'referencia_id': payload.get('referencia_id'),
        'producto_sku': sku,
        'producto_nombre': payload.get('producto_nombre', '') or '',
        'lote_numero': payload.get('lote_numero', '') or '',
        'cantidad': int(Decimal(str(payload.get('cantidad') or 0))),
        'cantidad_anterior': payload.get('cantidad_anterior'),
        'cantidad_nueva': payload.get('cantidad_nueva'),
        'costo_unitario': (
            Decimal(str(payload.get('costo_unitario')))
            if payload.get('costo_unitario') is not None else None
        ),
        'usuario_username': payload.get('usuario_username') or '',
        'notas': payload.get('notas', '') or '',
        'fecha_movimiento': fecha,
        'payload': payload,
    }

    if movimiento_id:
        InventarioMovimientoSync.objects.update_or_create(
            sucursal=sucursal,
            movimiento_id_local=movimiento_id,
            defaults=defaults,
        )
        return

    exists = InventarioMovimientoSync.objects.filter(
        sucursal=sucursal,
        referencia_tipo=defaults['referencia_tipo'],
        referencia_id=defaults['referencia_id'],
        producto_sku=sku,
        lote_numero=defaults['lote_numero'],
        tipo=defaults['tipo'],
    ).exists()
    if not exists:
        InventarioMovimientoSync.objects.create(
            sucursal=sucursal,
            movimiento_id_local=None,
            **defaults,
        )


# ---- COTIZACIONES ----

def _resolver_cliente_cotizacion(sucursal, payload):
    """Cliente de una cotizacion; cae al generico CONTADO si no hay forma.

    Se apoya en el resolutor compartido. El fallback historico "buscar por
    nombre exacto" se ELIMINA a proposito: fusionaba homonimos en silencio, y
    en una cotizacion que puede convertirse en venta a credito eso corrompe
    datos de cartera.
    """
    from apps.clientes.models import Cliente

    cliente = _resolver_o_crear_cliente(sucursal, payload)
    return cliente or Cliente.get_cliente_contado()


def _handler_cotizacion_creada(sucursal, payload):
    from apps.cotizaciones.models import Cotizacion, DetalleCotizacion
    from apps.productos.models import Producto

    numero = payload.get('numero_cotizacion')
    if not numero:
        raise ValueError('Payload sin numero_cotizacion')

    cliente = _resolver_cliente_cotizacion(sucursal, payload)
    usuario = _resolver_usuario(payload.get('usuario_username')) or sucursal.usuario_servicio
    fecha_creacion = (
        parse_datetime(payload['fecha_creacion'])
        if payload.get('fecha_creacion') else timezone.now()
    )

    cotizacion, _ = Cotizacion.objects.update_or_create(
        sucursal=sucursal,
        numero_cotizacion=numero,
        defaults={
            'cliente': cliente,
            'usuario': usuario,
            'fecha_creacion': fecha_creacion,
            'subtotal': Decimal(payload.get('subtotal', '0')),
            'descuento_total': Decimal(payload.get('descuento_total', '0')),
            'total': Decimal(payload.get('total', '0')),
            'estado': payload.get('estado', 'PENDIENTE'),
            'notas': payload.get('notas', '') or '',
        },
    )
    cotizacion.detalles.all().delete()
    for item in payload.get('detalles', []):
        sku = item.get('producto_sku')
        producto = Producto.objects.filter(sku=sku).first() if sku else None
        if producto is None:
            raise ValueError(f'Producto SKU {sku} no existe en cloud para cotizacion {numero}')
        DetalleCotizacion.objects.create(
            cotizacion=cotizacion,
            producto=producto,
            cantidad=int(item.get('cantidad') or 0),
            precio_unitario=Decimal(item.get('precio_unitario', '0')),
            subtotal=Decimal(item.get('subtotal', '0')),
            descuento_monto=Decimal(item.get('descuento_monto', '0')),
            descuento_porcentaje=Decimal(item.get('descuento_porcentaje', '0')),
            total_linea=Decimal(item.get('total_linea', '0')),
        )


def _handler_cotizacion_convertida(sucursal, payload):
    from apps.cotizaciones.models import Cotizacion
    from apps.ventas.models import Venta

    numero = payload.get('numero_cotizacion')
    if not numero:
        raise ValueError('Payload sin numero_cotizacion')

    cotizacion = Cotizacion.objects.filter(sucursal=sucursal, numero_cotizacion=numero).first()
    if cotizacion is None:
        _handler_cotizacion_creada(sucursal, payload)
        cotizacion = Cotizacion.objects.get(sucursal=sucursal, numero_cotizacion=numero)

    venta_numero = payload.get('venta_numero')
    venta = None
    if venta_numero:
        venta = Venta.objects.filter(numero_venta=venta_numero).first()
        if venta is None:
            raise ValueError(f'Venta {venta_numero} no existe en cloud todavia')

    cotizacion.estado = 'CONVERTIDA'
    cotizacion.venta = venta
    cotizacion.save(update_fields=['estado', 'venta'])


# ---- CUENTAS POR COBRAR ----

def _handler_cxc_creada(sucursal, payload):
    """Replica una cuenta por cobrar recibida desde sucursal."""
    from apps.clientes.models import Cliente
    from apps.cuentas_por_cobrar.models import CuentaPorCobrar, CuotaCxC, MetodoPlazoCredito
    from apps.ventas.models import Venta

    numero_venta = payload.get('numero_venta')
    if not numero_venta:
        raise ValueError('Payload CxC sin numero_venta')

    venta = Venta.objects.filter(numero_venta=numero_venta).first()
    if not venta:
        raise ValueError(f'Venta {numero_venta} no existe en cloud todavia')

    cuenta_existente = CuentaPorCobrar.objects.filter(venta=venta).first()
    if cuenta_existente:
        # Reenvio CORRECTIVO. Si la cuenta se creo a nombre del generico
        # CLIENTE CONTADO -- porque el payload de entonces no permitia
        # identificar al titular -- y ahora si se puede resolver, se corrige.
        # Sin esto, una CxC nacida en la ventana de despliegue quedaba con el
        # titular equivocado PARA SIEMPRE: el reenvio la saltaba por existir.
        if cuenta_existente.cliente.tipo == 'CONTADO':
            mejor = _resolver_o_crear_cliente(sucursal, payload)
            if mejor is not None and mejor.tipo != 'CONTADO':
                cuenta_existente.cliente = mejor
                cuenta_existente.save(update_fields=['cliente'])
                if venta.cliente_id is None:
                    venta.cliente = mejor
                    venta.save(update_fields=['cliente'])
                logger.info('CxC %s: titular corregido a %s en reenvio',
                            numero_venta, mejor.nombre)
                return
        logger.info('CxC para venta %s ya existe en cloud, skip', numero_venta)
        return

    cliente = _resolver_o_crear_cliente(sucursal, payload)
    if cliente is None and venta.cliente_id:
        cliente = venta.cliente
    if cliente is None:
        # Ultimo recurso: una cuenta por cobrar SIEMPRE necesita titular. Antes
        # aqui se lanzaba ValueError y el evento moria tras agotar reintentos
        # (BUG-C). Preferimos registrar la deuda contra el cliente generico y
        # que sea visible, a perderla en silencio.
        cliente = Cliente.get_cliente_contado()
        logger.warning(
            'CxC %s sin cliente resoluble; se asigna CLIENTE CONTADO', numero_venta
        )

    metodo_tipo = payload.get('metodo_plazo_tipo') or payload.get('modalidad') or MetodoPlazoCredito.TIPO_VENCIMIENTO_UNICO
    if metodo_tipo not in dict(MetodoPlazoCredito.TIPO_CHOICES):
        metodo_tipo = MetodoPlazoCredito.TIPO_VENCIMIENTO_UNICO
    frecuencia = payload.get('metodo_plazo_frecuencia') or MetodoPlazoCredito.FRECUENCIA_MENSUAL
    if frecuencia not in dict(MetodoPlazoCredito.FRECUENCIA_CHOICES):
        frecuencia = MetodoPlazoCredito.FRECUENCIA_MENSUAL
    try:
        dias_vencimiento = int(payload.get('metodo_plazo_dias_vencimiento') or 30)
    except (TypeError, ValueError):
        dias_vencimiento = 30
    try:
        cantidad_cuotas = int(
            payload.get('metodo_plazo_cantidad_cuotas')
            or max(len(payload.get('cuotas', [])), 1)
        )
    except (TypeError, ValueError):
        cantidad_cuotas = max(len(payload.get('cuotas', [])), 1)

    metodo, _ = MetodoPlazoCredito.objects.get_or_create(
        nombre=payload.get('metodo_plazo') or 'Credito importado',
        defaults={
            'tipo': metodo_tipo,
            'dias_vencimiento': max(dias_vencimiento, 1),
            'cantidad_cuotas': max(cantidad_cuotas, 1),
            'frecuencia': frecuencia,
            'activo': True,
        },
    )

    cuenta = CuentaPorCobrar.objects.create(
        cliente=cliente,
        venta=venta,
        metodo_plazo=metodo,
        total=Decimal(payload.get('total', '0')),
        monto_inicial=Decimal(payload.get('monto_inicial', '0')),
        # POS sin actualizar no manda interes: capital = total - inicial, interes 0
        saldo_original=Decimal(
            payload.get('saldo_original')
            or str(Decimal(payload.get('total', '0')) - Decimal(payload.get('monto_inicial', '0')))
        ),
        interes_porcentaje=Decimal(payload.get('interes_porcentaje') or '0'),
        monto_interes=Decimal(payload.get('monto_interes') or '0'),
        saldo=Decimal(payload.get('saldo', '0')),
        estado=payload.get('estado', 'ABIERTA'),
        fecha_emision=date.fromisoformat(payload['fecha_emision']),
        fecha_limite=date.fromisoformat(payload['fecha_limite']),
        creado_por=venta.usuario,
        sucursal=sucursal,
    )

    for cuota in payload.get('cuotas', []):
        CuotaCxC.objects.create(
            cuenta=cuenta,
            numero=cuota.get('numero'),
            monto=Decimal(cuota.get('monto', '0')),
            saldo=Decimal(cuota.get('saldo', '0')),
            fecha_vencimiento=date.fromisoformat(cuota['fecha_vencimiento']),
            estado=cuota.get('estado', 'PENDIENTE'),
        )


def _handler_cxc_pago(sucursal, payload):
    """Registra un pago CxC replicado desde sucursal."""
    from apps.cuentas_por_cobrar.models import CuentaPorCobrar, PagoCxC
    from apps.ventas.models import Venta

    numero_venta = payload.get('numero_venta')
    venta = Venta.objects.filter(numero_venta=numero_venta).first()
    if not venta:
        raise ValueError(f'Venta {numero_venta} no existe en cloud todavia')

    cuenta = CuentaPorCobrar.objects.filter(venta=venta).first()
    if not cuenta:
        raise ValueError(f'CxC de venta {numero_venta} no existe en cloud todavia')

    fecha_pago = parse_datetime(payload['fecha_pago']) if payload.get('fecha_pago') else timezone.now()
    monto = Decimal(payload.get('monto', '0'))
    if PagoCxC.objects.filter(cuenta=cuenta, fecha_pago=fecha_pago, monto=monto).exists():
        logger.info('Pago CxC para venta %s ya existe en cloud, skip', numero_venta)
        return

    PagoCxC.objects.create(
        cuenta=cuenta,
        metodo=payload.get('metodo', 'EFECTIVO'),
        monto=monto,
        referencia=payload.get('referencia', '') or '',
        fecha_pago=fecha_pago,
        registrado_por=_resolver_usuario(payload.get('registrado_por_username')) or cuenta.creado_por,
        estado=payload.get('estado', 'APLICADO'),
        aplicaciones=payload.get('aplicaciones') or [],
    )
    cuenta.saldo = Decimal(payload.get('saldo_cuenta', cuenta.saldo))
    cuenta.recalcular_estado(guardar=True)

    # Aplicar el snapshot de CUOTAS, no solo el saldo de la cabecera.
    #
    # Antes solo se movia `cuenta.saldo`: la cuenta cloud quedaba en 50 y sus
    # cuotas seguian sumando 90, todas pendientes. Aging, proxima cuota y
    # cualquier reporte por cuota contradecian el saldo de la misma cuenta — y
    # una anulacion posterior partia de datos ya divergentes.
    #
    # Las `aplicaciones` del payload no sirven para esto: referencian IDs de
    # cuota LOCALES. El snapshot viene identificado por `numero`, que si es
    # portable.
    _aplicar_snapshot_cuotas(cuenta, payload.get('cuotas'))


def _aplicar_snapshot_cuotas(cuenta, cuotas_payload):
    """
    Sincroniza las cuotas de una cuenta con el snapshot recibido.

    Se identifica cada cuota por `numero` (clave portable entre bases). Si el
    payload no trae cuotas —evento de una sucursal con codigo viejo— no se
    toca nada: es preferible dejar el estado anterior a borrarlo.
    """
    from apps.cuentas_por_cobrar.models import CuotaCxC

    if not cuotas_payload:
        return

    por_numero = {c.numero: c for c in cuenta.cuotas.all()}
    for fila in cuotas_payload:
        numero = fila.get('numero')
        cuota = por_numero.get(numero)
        if cuota is None:
            continue
        cuota.saldo = Decimal(str(fila.get('saldo', cuota.saldo)))
        cuota.estado = fila.get('estado', cuota.estado)
        fecha = fila.get('fecha_vencimiento')
        if fecha:
            cuota.fecha_vencimiento = parse_date(fecha) or cuota.fecha_vencimiento
        cuota.save(update_fields=['saldo', 'estado', 'fecha_vencimiento'])

    # Postcondicion: la suma de las cuotas debe cuadrar con el saldo de la
    # cuenta. Si no cuadra, el ledger cloud quedo divergente y hay que verlo.
    suma = sum(
        (c.saldo for c in cuenta.cuotas.all()), Decimal('0.00')
    )
    if abs(suma - cuenta.saldo) > Decimal('0.01'):
        logger.warning(
            '[SYNC] CxC %s: la suma de cuotas (%s) no coincide con el saldo '
            'de la cuenta (%s) despues de aplicar el snapshot.',
            cuenta.pk, suma, cuenta.saldo,
        )


def _handler_cxc_pago_anulado(sucursal, payload):
    """
    Replica la reversa de un abono CxC.

    Localiza el pago con el mismo matching que _handler_cxc_pago
    (cuenta + fecha_pago + monto) y aplica el snapshot post-reversa del
    payload (saldo/estado de cuenta y cuotas) sin recalcular nada local.
    """
    from apps.cuentas_por_cobrar.models import CuentaPorCobrar, PagoCxC
    from apps.ventas.models import Venta

    numero_venta = payload.get('numero_venta')
    venta = Venta.objects.filter(numero_venta=numero_venta).first()
    if not venta:
        raise ValueError(f'Venta {numero_venta} no existe en cloud todavia')

    cuenta = CuentaPorCobrar.objects.filter(venta=venta).first()
    if not cuenta:
        raise ValueError(f'CxC de venta {numero_venta} no existe en cloud todavia')

    fecha_pago = parse_datetime(payload['fecha_pago']) if payload.get('fecha_pago') else None
    monto = Decimal(payload.get('monto', '0'))
    pagos = PagoCxC.objects.filter(cuenta=cuenta, fecha_pago=fecha_pago, monto=monto)
    if not pagos.exists():
        raise ValueError(f'Pago CxC de venta {numero_venta} no existe en cloud todavia')

    # Idempotencia: si solo queda la version ANULADA, el evento ya se aplico
    pago = pagos.filter(estado='APLICADO').first()
    if pago is None:
        logger.info('Pago CxC de venta %s ya esta anulado en cloud, skip', numero_venta)
        return

    pago.estado = 'ANULADO'
    pago.anulado_por = _resolver_usuario(payload.get('anulado_por_username'))
    pago.fecha_anulacion = (
        parse_datetime(payload['fecha_anulacion'])
        if payload.get('fecha_anulacion') else timezone.now()
    )
    pago.motivo_anulacion = payload.get('motivo_anulacion', '') or ''
    pago.save(update_fields=['estado', 'anulado_por', 'fecha_anulacion', 'motivo_anulacion'])

    cuotas_payload = {c.get('numero'): c for c in payload.get('cuotas', [])}
    for cuota in cuenta.cuotas.all():
        snapshot = cuotas_payload.get(cuota.numero)
        if not snapshot:
            continue
        cuota.saldo = Decimal(snapshot.get('saldo', '0'))
        cuota.estado = snapshot.get('estado', cuota.estado)
        if cuota.saldo > Decimal('0.00'):
            cuota.fecha_pago = None
        cuota.save(update_fields=['saldo', 'estado', 'fecha_pago'])

    cuenta.saldo = Decimal(payload.get('saldo_cuenta', cuenta.saldo))
    cuenta.estado = payload.get('estado_cuenta', cuenta.estado)
    cuenta.save(update_fields=['saldo', 'estado', 'fecha_modificacion'])


def _handler_cxc_anulada(sucursal, payload):
    """Marca una CxC como anulada."""
    from apps.cuentas_por_cobrar.models import CuentaPorCobrar
    from apps.ventas.models import Venta

    numero_venta = payload.get('numero_venta')
    venta = Venta.objects.filter(numero_venta=numero_venta).first()
    if not venta:
        raise ValueError(f'Venta {numero_venta} no existe en cloud todavia')
    cuenta = CuentaPorCobrar.objects.filter(venta=venta).first()
    if cuenta:
        cuenta.marcar_anulada()


# ============================================================================
# Registry + Assert de integridad
# ============================================================================

HANDLERS = {
    'VENTA_CREADA': _handler_venta_creada,
    'VENTA_ANULADA': _handler_venta_anulada,
    'APERTURA_CAJA': _handler_apertura_caja,
    'MOVIMIENTO_CAJA': _handler_movimiento_caja,
    'CIERRE_CAJA': _handler_cierre_caja,
    'AJUSTE_INVENTARIO': _handler_ajuste_inventario,
    'COMPRA_REGISTRADA': _handler_compra,
    'INVENTARIO_MOVIMIENTO_REGISTRADO': _handler_movimiento_inventario,
    'INVENTARIO_SNAPSHOT': _handler_inventario_snapshot,
    'COTIZACION_CREADA': _handler_cotizacion_creada,
    'COTIZACION_CONVERTIDA': _handler_cotizacion_convertida,
    'CXC_CREADA': _handler_cxc_creada,
    'CXC_PAGO_REGISTRADO': _handler_cxc_pago,
    'CXC_PAGO_ANULADO': _handler_cxc_pago_anulado,
    'CXC_ANULADA': _handler_cxc_anulada,
}


# Sanity check: si alguien agrega un tipo nuevo en constants.py sin definir
# un handler aqui, Django NO arranca (error ruidoso al boot). Mejor que un
# error silencioso en produccion.
_tipos_sin_handler = set(TIPOS_EVENTO_CODIGOS) - set(HANDLERS.keys())
_tipos_extra_handler = set(HANDLERS.keys()) - set(TIPOS_EVENTO_CODIGOS)

if _tipos_sin_handler:
    raise ImportError(
        f'apps/api/views/sync.py: faltan handlers para tipos '
        f'{sorted(_tipos_sin_handler)}. Agregarlos al dict HANDLERS.'
    )

if _tipos_extra_handler:
    raise ImportError(
        f'apps/api/views/sync.py: handlers para tipos desconocidos '
        f'{sorted(_tipos_extra_handler)}. Revisar constants.py o quitarlos.'
    )
