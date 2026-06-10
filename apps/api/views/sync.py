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
    AJUSTE_INVENTARIO  -> Log-only por ahora (futuro: crear Ajuste en cloud)
    COMPRA_REGISTRADA  -> Log-only por ahora

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

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
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

        # Idempotencia: si ya procesamos este hash, responder DUPLICADO
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
# GET /api/v1/sync/roles/  — definiciones de rol del negocio (cloud -> sucursal)
# ============================================================================

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

    qs = Rol.objects.filter(negocio_id=negocio_id).prefetch_related('permisos').order_by('id')
    desde = request.query_params.get('desde')
    if desde:
        ts = parse_datetime(desde)
        if ts:
            qs = qs.filter(fecha_modificacion__gt=ts)

    data = [
        {
            'slug': r.slug,
            'nombre': r.nombre,
            'activo': r.activo,
            'permisos': sorted(p.codigo for p in r.permisos.all()),
            'fecha_modificacion': r.fecha_modificacion.isoformat(),
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
        .order_by('id')
    )
    desde = request.query_params.get('desde')
    if desde:
        ts = parse_datetime(desde)
        if ts:
            qs = qs.filter(fecha_modificacion__gt=ts)

    data = [
        {
            'usuario_username': a.usuario.username,
            'rol_slug': a.rol.slug,
            'sucursal_codigo': a.sucursal.codigo if a.sucursal_id else None,
            'activo': a.activo,
            'fecha_modificacion': a.fecha_modificacion.isoformat(),
        }
        for a in qs
    ]
    return Response(data)


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
    if tipo_evento in ('CXC_CREADA', 'CXC_ANULADA'):
        return payload.get('numero_venta', '')
    if tipo_evento == 'CXC_PAGO_REGISTRADO':
        return f"{payload.get('numero_venta', '')}-P{payload.get('pago_id_local', '?')}"
    return ''


def _buscar_turno_abierto(sucursal, caja_nombre, fecha_apertura):
    """
    Busca el TurnoCaja ABIERTO correspondiente a una sucursal+caja+apertura.

    Estrategia:
    1. Busca por caja+fecha_apertura exacta (lo mas preciso)
    2. Si no existe, busca el ultimo abierto de esa caja como fallback

    Retorna None si no existe. El caller decide que hacer.
    """
    from apps.caja.models import TurnoCaja

    try:
        caja = _obtener_caja(sucursal, caja_nombre)
    except Exception:
        return None

    fecha = parse_datetime(fecha_apertura) if fecha_apertura else None

    if fecha:
        turno = TurnoCaja.objects.filter(
            caja=caja,
            fecha_apertura=fecha,
        ).first()
        if turno:
            return turno

    # Fallback: ultimo turno abierto de la caja
    return TurnoCaja.objects.filter(caja=caja, estado='ABIERTO').first()


def _obtener_caja(sucursal, caja_nombre):
    """Obtiene o crea la caja con ese nombre en la sucursal."""
    from apps.caja.models import Caja
    caja, _ = Caja.objects.get_or_create(
        nombre=caja_nombre,
        sucursal=sucursal,
        defaults={'activa': True},
    )
    return caja


def _resolver_usuario(username):
    """Resuelve username -> User o None."""
    if not username:
        return None
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return User.objects.filter(username=username).first()


# ---- VENTAS ----

def _handler_venta_creada(sucursal, payload):
    """Crea Venta + DetalleVenta + Pago a partir del payload."""
    from apps.ventas.models import Venta, DetalleVenta, Pago
    from apps.productos.models import Producto
    from apps.clientes.models import Cliente

    numero_venta = payload.get('numero_venta')
    if not numero_venta:
        raise ValueError('Payload sin numero_venta')

    if Venta.objects.filter(numero_venta=numero_venta).exists():
        logger.info('Venta %s ya existe en cloud, skip', numero_venta)
        return

    usuario = _resolver_usuario(payload.get('usuario_username'))
    cliente = None
    if payload.get('cliente_cedula_rnc'):
        cliente = Cliente.objects.filter(cedula_rnc=payload['cliente_cedula_rnc']).first()

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
    )

    fecha = parse_datetime(payload['fecha_venta']) if payload.get('fecha_venta') else None
    if fecha:
        Venta.objects.filter(pk=venta.pk).update(fecha_venta=fecha)

    for d in payload.get('detalles', []):
        sku = d.get('producto_sku')
        producto = Producto.objects.filter(sku=sku).first() if sku else None
        if not producto:
            logger.warning(
                'Producto SKU %s no existe en cloud para venta %s; linea omitida',
                sku, numero_venta,
            )
            continue
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

    caja = _obtener_caja(sucursal, payload.get('caja_nombre', 'Caja Principal'))
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

    caja = _obtener_caja(sucursal, payload.get('caja_nombre', 'Caja Principal'))

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


# ---- INVENTARIO / COMPRAS (log-only por ahora) ----

def _handler_ajuste_inventario(sucursal, payload):
    """Log-only. Futuro: crear AjusteInventario en cloud con FK a sucursal."""
    logger.info(
        'AJUSTE_INVENTARIO: sucursal=%s producto=%s tipo=%s cant=%s',
        sucursal.codigo if sucursal else '?',
        payload.get('producto_sku'),
        payload.get('tipo'),
        payload.get('cantidad'),
    )


def _handler_compra(sucursal, payload):
    """Log-only por ahora."""
    logger.info(
        'COMPRA_REGISTRADA: sucursal=%s numero=%s total=%s',
        sucursal.codigo if sucursal else '?',
        payload.get('numero_compra'),
        payload.get('total'),
    )


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

    if CuentaPorCobrar.objects.filter(venta=venta).exists():
        logger.info('CxC para venta %s ya existe en cloud, skip', numero_venta)
        return

    cliente = None
    if payload.get('cliente_cedula_rnc'):
        cliente = Cliente.objects.filter(cedula_rnc=payload['cliente_cedula_rnc']).first()
    if cliente is None and venta.cliente_id:
        cliente = venta.cliente
    if cliente is None:
        raise ValueError(f'Cliente de CxC {numero_venta} no existe en cloud')

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
