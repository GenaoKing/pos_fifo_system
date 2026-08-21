import json
import logging
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Count, DecimalField, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.auditoria.models import get_client_ip
from apps.permisos.decorators import requiere_permiso_json, requiere_permiso_local
from apps.clientes.models import Cliente
from apps.ventas.services import ErrorVentaBase

from .models import CuentaPorCobrar, MetodoPlazoCredito, PagoCxC

logger = logging.getLogger('cuentas_por_cobrar')
from .services import anular_pago_cxc_service, registrar_pago_cxc_service, resumen_credito_cliente


def _decimal_str(value):
    return str(value or Decimal('0.00'))


def _cuenta_data(cuenta):
    cuotas = list(cuenta.cuotas.all())
    pagos = sorted(cuenta.pagos_cxc.all(), key=lambda p: p.id, reverse=True)
    ultimo_aplicado_id = next(
        (p.id for p in pagos if p.estado == PagoCxC.ESTADO_APLICADO),
        None,
    )
    return {
        'id': cuenta.id,
        'venta_id': cuenta.venta_id,
        'numero_venta': cuenta.venta.numero_venta,
        'cliente_id': cuenta.cliente_id,
        'cliente': cuenta.cliente.nombre,
        'cedula_rnc': cuenta.cliente.cedula_rnc or '',
        'total': _decimal_str(cuenta.total),
        'monto_inicial': _decimal_str(cuenta.monto_inicial),
        'saldo_original': _decimal_str(cuenta.saldo_original),
        'interes_porcentaje': _decimal_str(cuenta.interes_porcentaje),
        'monto_interes': _decimal_str(cuenta.monto_interes),
        'saldo': _decimal_str(cuenta.saldo),
        'estado': cuenta.estado,
        # Estado EFECTIVO: `estado` solo se recalcula por eventos (abono,
        # anulacion), asi que una cuenta abierta cuya fecha limite ya paso
        # seguia figurando ABIERTA hasta que alguien la tocara. Cobranza y
        # exportaciones mostraban deuda vencida como al dia.
        'estado_efectivo': (
            CuentaPorCobrar.ESTADO_VENCIDA if cuenta.esta_vencida else cuenta.estado
        ),
        'vencida_de_hecho': cuenta.esta_vencida,
        'metodo_plazo': cuenta.metodo_plazo.nombre,
        'fecha_emision': cuenta.fecha_emision.isoformat(),
        'fecha_limite': cuenta.fecha_limite.isoformat(),
        'cuotas': [
            {
                'id': c.id,
                'numero': c.numero,
                'monto': _decimal_str(c.monto),
                'saldo': _decimal_str(c.saldo),
                'fecha_vencimiento': c.fecha_vencimiento.isoformat(),
                'estado': c.estado,
            }
            for c in cuotas
        ],
        'pagos': [
            {
                'id': p.id,
                'metodo': p.metodo,
                'monto': _decimal_str(p.monto),
                'referencia': p.referencia or '',
                'fecha_pago': p.fecha_pago.strftime('%d/%m/%Y %H:%M'),
                'estado': p.estado,
                'registrado_por': p.registrado_por.username if p.registrado_por_id else '',
                'motivo_anulacion': p.motivo_anulacion or '',
                'es_ultimo_aplicado': p.id == ultimo_aplicado_id,
            }
            for p in pagos
        ],
    }


def cuentas_en_alcance(request):
    """
    Queryset de CxC acotado a la sucursal efectiva del usuario.

    Las vistas partian de TODAS las cuentas y resolvian los IDs contra ese
    universo global: un rol asignado solo a la sucursal A podia listar, cobrar
    y anular cuentas de B. El permiso decia "puede cobrar", pero no "puede
    cobrar AQUI".

    Una cuenta sin sucursal (legacy, anterior a la Fase 2) queda visible para
    todos: darla por ajena la volveria inaccesible en instalaciones que todavia
    no migraron.
    """
    base = CuentaPorCobrar.objects.select_related('cliente', 'venta', 'metodo_plazo')

    sucursal = getattr(request, 'sucursal', None)
    if sucursal is None:
        return base

    # Acceso global explicito: un rol con el permiso consolidado ve toda la red.
    if request.user.tiene_permiso('reportes.consolidado.ver', sucursal=sucursal):
        return base

    return base.filter(Q(sucursal=sucursal) | Q(sucursal__isnull=True))


def _pago_en_alcance(request, pago_id):
    """True si el abono pertenece a una cuenta dentro del alcance del usuario."""
    return PagoCxC.objects.filter(
        id=pago_id, cuenta__in=cuentas_en_alcance(request),
    ).exists()


def _metodos_credito_en_alcance(sucursal):
    """
    Metodos de la sucursal, con fallback a los globales.

    El fallback anterior tomaba "el primer metodo activo del tipo por ID", sin
    mirar la sucursal: con el metodo de B creado antes que el de A, una venta
    de A aplicaba las condiciones de B.
    """
    base = MetodoPlazoCredito.objects.filter(activo=True)
    if sucursal is None:
        return base.filter(sucursal__isnull=True).order_by('nombre')

    propios = base.filter(sucursal=sucursal)
    if propios.exists():
        return propios.order_by('nombre')
    return base.filter(sucursal__isnull=True).order_by('nombre')


def obtener_cuenta_en_alcance(request, cuenta_id):
    """Resuelve un ID CONTRA el alcance; fuera de el, 404."""
    return get_object_or_404(cuentas_en_alcance(request), id=cuenta_id)


@login_required
@requiere_permiso_local('cuentas_por_cobrar.ver')
def lista_cuentas(request):
    estado = request.GET.get('estado') or ''
    query = request.GET.get('q', '').strip()

    cuentas = (
        cuentas_en_alcance(request)
        .prefetch_related('cuotas', 'pagos_cxc')
        .exclude(estado=CuentaPorCobrar.ESTADO_ANULADA)
    )
    if estado == CuentaPorCobrar.ESTADO_VENCIDA:
        # Vencida DE HECHO, no solo las marcadas: la cuenta que cruzo su fecha
        # limite sin ningun movimiento posterior conserva el estado anterior.
        cuentas = cuentas.filter(
            estado__in=CuentaPorCobrar.ESTADOS_ABIERTOS,
            fecha_limite__lt=timezone.localdate(),
        )
    elif estado in (CuentaPorCobrar.ESTADO_ABIERTA, CuentaPorCobrar.ESTADO_PARCIAL):
        # Y al reves: lo que ya vencio no debe aparecer como "abierta".
        cuentas = cuentas.filter(estado=estado).exclude(
            fecha_limite__lt=timezone.localdate(),
        )
    elif estado:
        cuentas = cuentas.filter(estado=estado)
    else:
        # El filtro vacio se llama "Estados abiertos" en la interfaz, pero el
        # backend incluia las PAGADAS: 300 cuentas saldadas recientes podian
        # empujar fuera de la lista una deuda antigua que el resumen SI
        # contaba. El default ahora es lo que dice ser.
        cuentas = cuentas.filter(estado__in=CuentaPorCobrar.ESTADOS_ABIERTOS)
    if query:
        cuentas = cuentas.filter(
            Q(cliente__nombre__icontains=query)
            | Q(cliente__cedula_rnc__icontains=query)
            | Q(venta__numero_venta__icontains=query)
        )

    resumen = cuentas.aggregate(
        total_saldo=Coalesce(Sum('saldo'), Decimal('0.00'), output_field=DecimalField()),
        cantidad=Count('id'),
    )

    # El corte a 300 era silencioso: el resumen mostraba deuda que no estaba en
    # la lista. Ahora se informa cuantas quedaron fuera.
    TOPE = 300
    visibles = list(cuentas[:TOPE])
    ocultas = max(resumen['cantidad'] - len(visibles), 0)

    context = {
        'cuentas_json': json.dumps([_cuenta_data(c) for c in visibles]),
        'resumen': resumen,
        'cuentas_ocultas': ocultas,
        'tope_lista': TOPE,
        'estado_filtro': estado,
        'query': query,
        'puede_cobrar': request.user.tiene_permiso('cuentas_por_cobrar.cobrar'),
        'puede_anular_pago': request.user.tiene_permiso('cuentas_por_cobrar.anular_pago'),
    }
    return render(request, 'cuentas_por_cobrar/lista.html', context)


@login_required
@requiere_permiso_local('cuentas_por_cobrar.ver')
def estado_cuenta_cliente(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    cuentas = (
        cuentas_en_alcance(request)
        .filter(cliente=cliente)
        .prefetch_related('cuotas', 'pagos_cxc')
        .order_by('-fecha_emision', '-id')
    )
    resumen = resumen_credito_cliente(cliente)
    context = {
        'cliente': cliente,
        'cuentas_json': json.dumps([_cuenta_data(c) for c in cuentas]),
        'resumen_credito': resumen,
        'resumen_credito_json': json.dumps({k: str(v) if v is not None else None for k, v in resumen.items()}),
        'puede_cobrar': request.user.tiene_permiso('cuentas_por_cobrar.cobrar'),
        'puede_anular_pago': request.user.tiene_permiso('cuentas_por_cobrar.anular_pago'),
    }
    return render(request, 'cuentas_por_cobrar/cliente.html', context)


def _cuentas_cliente_export(cliente):
    return (
        CuentaPorCobrar.objects
        .filter(cliente=cliente)
        .select_related('cliente', 'venta', 'metodo_plazo')
        .prefetch_related('cuotas', 'pagos_cxc')
        .order_by('-fecha_emision', '-id')
    )


@login_required
@requiere_permiso_local('cuentas_por_cobrar.ver')
@require_http_methods(['GET'])
def estado_cuenta_cliente_pdf(request, cliente_id):
    from .pdf_generator import EstadoCuentaPDF

    cliente = get_object_or_404(Cliente, id=cliente_id)
    cuentas = _cuentas_cliente_export(cliente)
    resumen = resumen_credito_cliente(cliente)
    buffer = EstadoCuentaPDF(cliente, cuentas, resumen).generar()

    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="estado_cuenta_{cliente.id}_{timezone.localdate().isoformat()}.pdf"'
    )
    return response


@login_required
@requiere_permiso_local('cuentas_por_cobrar.ver')
@require_http_methods(['GET'])
def estado_cuenta_cliente_excel(request, cliente_id):
    from .excel_generator import generar_estado_cuenta_xlsx

    cliente = get_object_or_404(Cliente, id=cliente_id)
    cuentas = _cuentas_cliente_export(cliente)
    resumen = resumen_credito_cliente(cliente)
    buffer = generar_estado_cuenta_xlsx(cliente, cuentas, resumen)

    response = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = (
        f'attachment; filename="estado_cuenta_{cliente.id}_{timezone.localdate().isoformat()}.xlsx"'
    )
    return response


@login_required
@requiere_permiso_json('cuentas_por_cobrar.ver')
@require_http_methods(['GET'])
def api_metodos_credito(request):
    """
    Metodos de credito aplicables A ESTA SUCURSAL.

    Devolvia todos los metodos activos a cualquier usuario autenticado, sin
    exigir siquiera un permiso de CxC. Ademas, una sucursal podia terminar
    aplicando el interes, el inicial o el plazo definidos para otra.
    """
    metodos = _metodos_credito_en_alcance(getattr(request, 'sucursal', None))
    return JsonResponse({
        'success': True,
        'metodos': [
            {
                'id': m.id,
                'nombre': m.nombre,
                'tipo': m.tipo,
                'dias_vencimiento': m.dias_vencimiento,
                'cantidad_cuotas': m.cantidad_cuotas,
                'frecuencia': m.frecuencia,
                'inicial_minima_porcentaje': str(m.inicial_minima_porcentaje),
                'interes_porcentaje': str(m.interes_porcentaje),
            }
            for m in metodos
        ],
    })


@login_required
@requiere_permiso_json('cuentas_por_cobrar.ver')
@require_http_methods(['GET'])
def api_resumen_cliente(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id, activo=True)
    resumen = resumen_credito_cliente(cliente)
    return JsonResponse({
        'success': True,
        'cliente_id': cliente.id,
        'limite_credito': str(resumen['limite_credito']),
        'plazo_credito_dias': resumen['plazo_credito_dias'],
        'saldo_pendiente': str(resumen['saldo_pendiente']),
        'credito_disponible': str(resumen['credito_disponible']),
        'monto_vencido': str(resumen['monto_vencido']),
        'proximo_vencimiento': resumen['proximo_vencimiento'].isoformat() if resumen['proximo_vencimiento'] else None,
    })


@login_required
@requiere_permiso_json('cuentas_por_cobrar.cobrar')
@require_http_methods(['POST'])
def api_registrar_pago(request):
    """
    Registra un abono.

    Acepta `clave_idempotencia` (UUID generado por el cliente): un reintento
    con la misma clave devuelve el abono original en vez de cobrar dos veces.
    Sin ella, dos POST identicos —un doble click, un timeout, el reintento de
    un proxy— creaban dos abonos y reducian el saldo dos veces.
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse(
            {'success': False, 'error': 'JSON invalido en el request.'}, status=400,
        )

    try:
        monto = Decimal(str(data.get('monto', 0)))
    except (InvalidOperation, TypeError, ValueError):
        # Antes esto caia en el `except Exception` y salia como 500 con el
        # texto de la excepcion: un dato mal formado parecia una falla del
        # servidor y filtraba detalle interno.
        return JsonResponse(
            {'success': False, 'error': 'El monto no es un numero valido.'},
            status=400,
        )

    # La cuenta se resuelve CONTRA el alcance del usuario: un ID de otra
    # sucursal responde 404, no cobra.
    cuenta_solicitada = cuentas_en_alcance(request).filter(
        id=data.get('cuenta_id')
    ).first()
    if cuenta_solicitada is None:
        return JsonResponse(
            {'success': False, 'error': 'Cuenta por cobrar no encontrada.'},
            status=404,
        )

    try:
        pago = registrar_pago_cxc_service(
            cuenta_id=cuenta_solicitada.id,
            usuario=request.user,
            metodo=data.get('metodo'),
            monto=monto,
            referencia=data.get('referencia', ''),
            notas=data.get('notas', ''),
            ip_address=get_client_ip(request),
            clave_idempotencia=data.get('clave_idempotencia'),
        )
    except ErrorVentaBase as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=exc.status_code)
    except CuentaPorCobrar.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Cuenta por cobrar no encontrada.'}, status=404)
    except Exception:
        logger.exception('Error inesperado registrando un abono CxC')
        return JsonResponse(
            {'success': False, 'error': 'Error inesperado al registrar el abono.'},
            status=500,
        )

    # Recibo best-effort: el abono ya quedo registrado aunque la impresora falle.
    try:
        from utils.impresoras.manager import print_manager
        impresion = print_manager.print_recibo_cxc(pago, request.user)
    except Exception as exc:
        impresion = {'success': False, 'mensaje': str(exc)}

    cuenta = pago.cuenta
    cuenta.refresh_from_db()
    return JsonResponse({
        'success': True,
        'pago': {
            'id': pago.id,
            'monto': str(pago.monto),
            'metodo': pago.metodo,
            'fecha_pago': pago.fecha_pago.strftime('%d/%m/%Y %H:%M'),
        },
        'cuenta': {
            'id': cuenta.id,
            'saldo': str(cuenta.saldo),
            'estado': cuenta.estado,
        },
        'impresion': impresion,
    })


@login_required
@requiere_permiso_json('cuentas_por_cobrar.anular_pago')
@require_http_methods(['POST'])
def api_anular_pago(request, pago_id):
    if not _pago_en_alcance(request, pago_id):
        return JsonResponse(
            {'success': False, 'error': 'Abono no encontrado.'}, status=404,
        )

    try:
        data = json.loads(request.body or '{}')
        pago = anular_pago_cxc_service(
            pago_id=pago_id,
            usuario=request.user,
            motivo=data.get('motivo', ''),
            ip_address=get_client_ip(request),
        )
    except ErrorVentaBase as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=exc.status_code)
    except PagoCxC.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Abono no encontrado.'}, status=404)
    except Exception:
        logger.exception('Error inesperado anulando un abono CxC')
        return JsonResponse(
            {'success': False, 'error': 'Error inesperado al anular el abono.'},
            status=500,
        )

    cuenta = pago.cuenta
    cuenta.refresh_from_db()
    return JsonResponse({
        'success': True,
        'pago': {
            'id': pago.id,
            'estado': pago.estado,
            'motivo_anulacion': pago.motivo_anulacion,
        },
        'cuenta': {
            'id': cuenta.id,
            'saldo': str(cuenta.saldo),
            'estado': cuenta.estado,
        },
    })


@login_required
@requiere_permiso_json('cuentas_por_cobrar.ver')
@require_http_methods(['POST'])
def api_imprimir_recibo(request, pago_id):
    # Reimprimir un recibo expone datos del cliente y del cobro: se resuelve
    # contra el alcance, igual que cobrar y anular.
    pago = get_object_or_404(
        PagoCxC.objects.select_related('cuenta', 'cuenta__venta', 'cuenta__cliente', 'registrado_por'),
        id=pago_id,
        cuenta__in=cuentas_en_alcance(request),
    )
    try:
        from utils.impresoras.manager import print_manager
        resultado = print_manager.print_recibo_cxc(pago, request.user, reimpresion=True)
    except Exception as exc:
        resultado = {'success': False, 'mensaje': str(exc)}
    status = 200 if resultado.get('success') else 502
    return JsonResponse(resultado, status=status)
