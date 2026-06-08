import json
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, DecimalField, Q, Sum
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from apps.auditoria.models import get_client_ip
from apps.clientes.models import Cliente
from apps.ventas.services import ErrorVentaBase

from .models import CuentaPorCobrar, MetodoPlazoCredito
from .services import registrar_pago_cxc_service, resumen_credito_cliente


def _decimal_str(value):
    return str(value or Decimal('0.00'))


def _cuenta_data(cuenta):
    cuotas = list(cuenta.cuotas.all())
    return {
        'id': cuenta.id,
        'venta_id': cuenta.venta_id,
        'numero_venta': cuenta.venta.numero_venta,
        'cliente_id': cuenta.cliente_id,
        'cliente': cuenta.cliente.nombre,
        'cedula_rnc': cuenta.cliente.cedula_rnc or '',
        'total': _decimal_str(cuenta.total),
        'monto_inicial': _decimal_str(cuenta.monto_inicial),
        'saldo': _decimal_str(cuenta.saldo),
        'estado': cuenta.estado,
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
    }


@login_required
def lista_cuentas(request):
    estado = request.GET.get('estado') or ''
    query = request.GET.get('q', '').strip()

    cuentas = (
        CuentaPorCobrar.objects
        .select_related('cliente', 'venta', 'metodo_plazo')
        .prefetch_related('cuotas')
        .exclude(estado=CuentaPorCobrar.ESTADO_ANULADA)
    )
    if estado:
        cuentas = cuentas.filter(estado=estado)
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

    context = {
        'cuentas_json': json.dumps([_cuenta_data(c) for c in cuentas[:300]]),
        'resumen': resumen,
        'estado_filtro': estado,
        'query': query,
    }
    return render(request, 'cuentas_por_cobrar/lista.html', context)


@login_required
def estado_cuenta_cliente(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    cuentas = (
        CuentaPorCobrar.objects
        .filter(cliente=cliente)
        .select_related('cliente', 'venta', 'metodo_plazo')
        .prefetch_related('cuotas', 'pagos_cxc')
        .order_by('-fecha_emision', '-id')
    )
    resumen = resumen_credito_cliente(cliente)
    context = {
        'cliente': cliente,
        'cuentas_json': json.dumps([_cuenta_data(c) for c in cuentas]),
        'resumen_credito': resumen,
        'resumen_credito_json': json.dumps({k: str(v) if v is not None else None for k, v in resumen.items()}),
    }
    return render(request, 'cuentas_por_cobrar/cliente.html', context)


@login_required
@require_http_methods(['GET'])
def api_metodos_credito(request):
    metodos = MetodoPlazoCredito.objects.filter(activo=True).order_by('nombre')
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
            }
            for m in metodos
        ],
    })


@login_required
@require_http_methods(['GET'])
def api_resumen_cliente(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id, activo=True)
    resumen = resumen_credito_cliente(cliente)
    return JsonResponse({
        'success': True,
        'cliente_id': cliente.id,
        'limite_credito': str(resumen['limite_credito']),
        'saldo_pendiente': str(resumen['saldo_pendiente']),
        'credito_disponible': str(resumen['credito_disponible']),
        'monto_vencido': str(resumen['monto_vencido']),
        'proximo_vencimiento': resumen['proximo_vencimiento'].isoformat() if resumen['proximo_vencimiento'] else None,
    })


@login_required
@require_http_methods(['POST'])
def api_registrar_pago(request):
    try:
        data = json.loads(request.body)
        pago = registrar_pago_cxc_service(
            cuenta_id=data.get('cuenta_id'),
            usuario=request.user,
            metodo=data.get('metodo'),
            monto=Decimal(str(data.get('monto', 0))),
            referencia=data.get('referencia', ''),
            notas=data.get('notas', ''),
            ip_address=get_client_ip(request),
        )
    except ErrorVentaBase as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=exc.status_code)
    except CuentaPorCobrar.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Cuenta por cobrar no encontrada.'}, status=404)
    except Exception as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=500)

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
    })
