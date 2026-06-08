from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.db import models, transaction
from django.utils import timezone

from apps.auditoria.models import Auditoria
from apps.sync import events as sync_events
from apps.ventas.services.exceptions import (
    ClienteCreditoInvalidoError,
    LimiteCreditoExcedidoError,
    MetodoPlazoCreditoInvalidoError,
    PermisoDenegadoError,
)

from .models import CuentaPorCobrar, CuotaCxC, MetodoPlazoCredito, PagoCxC


DOS_DECIMALES = Decimal('0.01')


def _q(value: Decimal | str | float | int) -> Decimal:
    return Decimal(str(value or 0)).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def saldo_pendiente_cliente(cliente) -> Decimal:
    return CuentaPorCobrar.objects.filter(
        cliente=cliente,
        estado__in=(
            CuentaPorCobrar.ESTADO_ABIERTA,
            CuentaPorCobrar.ESTADO_PARCIAL,
            CuentaPorCobrar.ESTADO_VENCIDA,
        ),
    ).aggregate(total=models.Sum('saldo'))['total'] or Decimal('0.00')


def resumen_credito_cliente(cliente) -> dict:
    saldo = saldo_pendiente_cliente(cliente)
    limite = Decimal(str(cliente.limite_credito or 0))
    hoy = timezone.localdate()
    vencido = CuentaPorCobrar.objects.filter(
        cliente=cliente,
        estado__in=(CuentaPorCobrar.ESTADO_ABIERTA, CuentaPorCobrar.ESTADO_PARCIAL, CuentaPorCobrar.ESTADO_VENCIDA),
        fecha_limite__lt=hoy,
    ).aggregate(total=models.Sum('saldo'))['total'] or Decimal('0.00')
    proxima = CuotaCxC.objects.filter(
        cuenta__cliente=cliente,
        estado__in=(CuotaCxC.ESTADO_PENDIENTE, CuotaCxC.ESTADO_PARCIAL, CuotaCxC.ESTADO_VENCIDA),
    ).order_by('fecha_vencimiento').first()
    return {
        'limite_credito': limite,
        'saldo_pendiente': saldo,
        'credito_disponible': limite - saldo,
        'monto_vencido': vencido,
        'proximo_vencimiento': proxima.fecha_vencimiento if proxima else None,
    }


def _validar_cliente_credito(cliente):
    if cliente is None or not cliente.activo or cliente.es_contado:
        raise ClienteCreditoInvalidoError('La venta a credito requiere un cliente real activo.')


def _obtener_metodo(metodo_plazo_id) -> MetodoPlazoCredito:
    try:
        return MetodoPlazoCredito.objects.get(id=metodo_plazo_id, activo=True)
    except MetodoPlazoCredito.DoesNotExist:
        raise MetodoPlazoCreditoInvalidoError('Metodo de plazo de credito no encontrado o inactivo.')


def _obtener_admin_override(admin_override_id):
    if not admin_override_id:
        return None
    from apps.usuarios.models import Usuario

    try:
        admin = Usuario.objects.get(id=admin_override_id, rol__in=('ADMIN', 'SYSADMIN'), activo=True)
    except Usuario.DoesNotExist:
        raise PermisoDenegadoError('El administrador de override no existe o esta inactivo.')
    return admin


def _fechas_cuotas(
    *,
    metodo: MetodoPlazoCredito,
    cantidad_cuotas: int,
    fecha_primer_vencimiento: date | None,
) -> list[date]:
    primera = fecha_primer_vencimiento or (timezone.localdate() + timedelta(days=metodo.dias_vencimiento))
    if cantidad_cuotas == 1:
        return [primera]
    intervalo = metodo.dias_entre_cuotas()
    return [primera + timedelta(days=intervalo * i) for i in range(cantidad_cuotas)]


def _montos_cuotas(saldo: Decimal, cantidad_cuotas: int) -> list[Decimal]:
    base = (saldo / Decimal(cantidad_cuotas)).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)
    montos = [base for _ in range(cantidad_cuotas)]
    diferencia = saldo - sum(montos, Decimal('0.00'))
    montos[-1] = _q(montos[-1] + diferencia)
    return montos


def crear_cuenta_para_venta(
    *,
    venta,
    usuario,
    credito_data: dict[str, Any],
    ip_address: str | None = None,
) -> CuentaPorCobrar:
    cliente = venta.cliente
    _validar_cliente_credito(cliente)

    metodo = _obtener_metodo(credito_data.get('metodo_plazo_id'))
    monto_inicial = _q(credito_data.get('monto_inicial', 0))
    total = _q(venta.total)
    saldo_credito = _q(total - monto_inicial)

    if monto_inicial < Decimal('0.00') or monto_inicial >= total:
        raise MetodoPlazoCreditoInvalidoError('El inicial debe ser menor al total y no puede ser negativo.')

    inicial_minimo = _q(total * (metodo.inicial_minima_porcentaje / Decimal('100')))
    if monto_inicial < inicial_minimo:
        raise MetodoPlazoCreditoInvalidoError(
            f'El inicial minimo para {metodo.nombre} es ${inicial_minimo}.'
        )

    saldo_actual = saldo_pendiente_cliente(cliente)
    limite = Decimal(str(cliente.limite_credito or 0))
    admin_override = _obtener_admin_override(credito_data.get('admin_override_id'))

    if saldo_actual + saldo_credito > limite and admin_override is None:
        disponible = limite - saldo_actual
        raise LimiteCreditoExcedidoError(
            f'Limite de credito excedido. Disponible: ${disponible}, saldo nuevo: ${saldo_credito}. '
            'Requiere autorizacion ADMIN/SYSADMIN.'
        )

    cantidad_cuotas = metodo.normalizar_cantidad_cuotas(credito_data.get('cantidad_cuotas'))
    fecha_primer_vencimiento = _parse_date(credito_data.get('fecha_primer_vencimiento'))
    fechas = _fechas_cuotas(
        metodo=metodo,
        cantidad_cuotas=cantidad_cuotas,
        fecha_primer_vencimiento=fecha_primer_vencimiento,
    )
    montos = _montos_cuotas(saldo_credito, cantidad_cuotas)

    cuenta = CuentaPorCobrar.objects.create(
        cliente=cliente,
        venta=venta,
        metodo_plazo=metodo,
        total=total,
        monto_inicial=monto_inicial,
        saldo=saldo_credito,
        fecha_limite=fechas[-1],
        creado_por=usuario,
        override_autorizado_por=admin_override,
        motivo_override=(credito_data.get('motivo_override') or '').strip(),
        sucursal=venta.sucursal,
    )

    for idx, (fecha_vencimiento, monto) in enumerate(zip(fechas, montos), start=1):
        CuotaCxC.objects.create(
            cuenta=cuenta,
            numero=idx,
            monto=monto,
            saldo=monto,
            fecha_vencimiento=fecha_vencimiento,
        )

    if admin_override:
        Auditoria.registrar(
            accion=Auditoria.TipoAccion.CONFIGURACION,
            descripcion=f'Override de limite de credito para venta {venta.numero_venta}',
            usuario=usuario,
            content_object=cuenta,
            metadata={
                'cliente_id': cliente.id,
                'saldo_anterior': str(saldo_actual),
                'saldo_nuevo': str(saldo_credito),
                'limite_credito': str(limite),
                'autorizado_por': admin_override.username,
            },
            ip_address=ip_address,
            nivel_importancia=Auditoria.NivelImportancia.CRITICA,
        )

    Auditoria.registrar(
        accion=Auditoria.TipoAccion.CREAR,
        descripcion=f'CXC creada para venta {venta.numero_venta} - saldo ${cuenta.saldo}',
        usuario=usuario,
        content_object=cuenta,
        datos_nuevos={
            'venta': venta.numero_venta,
            'cliente': cliente.nombre,
            'total': str(cuenta.total),
            'saldo': str(cuenta.saldo),
            'cuotas': cantidad_cuotas,
        },
        ip_address=ip_address,
        nivel_importancia=Auditoria.NivelImportancia.ALTA,
    )

    transaction.on_commit(lambda c=cuenta: sync_events.evento_cxc_creada(c))
    return cuenta


def registrar_pago_cxc_service(
    *,
    cuenta_id: int,
    usuario,
    metodo: str,
    monto: Decimal,
    referencia: str = '',
    notas: str = '',
    ip_address: str | None = None,
) -> PagoCxC:
    monto = _q(monto)
    if monto <= 0:
        raise MetodoPlazoCreditoInvalidoError('El monto del abono debe ser mayor a cero.')

    metodo = (metodo or '').upper()
    if metodo not in dict(PagoCxC.METODO_CHOICES):
        raise MetodoPlazoCreditoInvalidoError('Metodo de pago CxC invalido.')

    with transaction.atomic():
        cuenta = (
            CuentaPorCobrar.objects
            .select_for_update()
            .select_related('cliente', 'venta')
            .get(id=cuenta_id)
        )
        if not cuenta.esta_abierta:
            raise MetodoPlazoCreditoInvalidoError('La cuenta no acepta abonos en su estado actual.')
        if monto > cuenta.saldo:
            raise MetodoPlazoCreditoInvalidoError('El abono no puede ser mayor al saldo pendiente.')

        restante = monto
        aplicaciones = []
        cuotas = (
            cuenta.cuotas
            .select_for_update()
            .filter(estado__in=(CuotaCxC.ESTADO_PENDIENTE, CuotaCxC.ESTADO_PARCIAL, CuotaCxC.ESTADO_VENCIDA))
            .order_by('fecha_vencimiento', 'numero')
        )
        for cuota in cuotas:
            if restante <= 0:
                break
            aplicado = min(cuota.saldo, restante)
            cuota.saldo = _q(cuota.saldo - aplicado)
            cuota.recalcular_estado(guardar=True)
            restante = _q(restante - aplicado)
            aplicaciones.append({
                'cuota_id': cuota.id,
                'numero': cuota.numero,
                'monto': str(aplicado),
            })

        cuenta.saldo = _q(cuenta.saldo - monto)
        cuenta.recalcular_estado(guardar=True)

        pago = PagoCxC.objects.create(
            cuenta=cuenta,
            metodo=metodo,
            monto=monto,
            referencia=(referencia or '').strip(),
            registrado_por=usuario,
            aplicaciones=aplicaciones,
            notas=(notas or '').strip(),
        )

        Auditoria.registrar(
            accion=Auditoria.TipoAccion.CREAR,
            descripcion=f'Abono CxC registrado para {cuenta.venta.numero_venta} - ${monto}',
            usuario=usuario,
            content_object=pago,
            datos_nuevos={
                'cuenta_id': cuenta.id,
                'cliente': cuenta.cliente.nombre,
                'monto': str(monto),
                'metodo': metodo,
                'saldo_restante': str(cuenta.saldo),
            },
            ip_address=ip_address,
            nivel_importancia=Auditoria.NivelImportancia.ALTA,
        )

        transaction.on_commit(lambda p=pago: sync_events.evento_cxc_pago_registrado(p))

    return pago


def anular_cuenta_por_venta(*, venta, usuario=None, ip_address: str | None = None):
    cuenta = getattr(venta, 'cuenta_por_cobrar', None)
    if not cuenta or cuenta.estado == CuentaPorCobrar.ESTADO_ANULADA:
        return None

    cuenta.marcar_anulada()
    Auditoria.registrar(
        accion=Auditoria.TipoAccion.EDITAR,
        descripcion=f'CXC anulada por anulacion de venta {venta.numero_venta}',
        usuario=usuario,
        content_object=cuenta,
        metadata={'venta': venta.numero_venta},
        ip_address=ip_address,
        nivel_importancia=Auditoria.NivelImportancia.CRITICA,
    )
    transaction.on_commit(lambda c=cuenta: sync_events.evento_cxc_anulada(c))
    return cuenta
