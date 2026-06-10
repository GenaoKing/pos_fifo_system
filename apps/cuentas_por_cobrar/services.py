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
MODALIDAD_VENCIMIENTO_UNICO = MetodoPlazoCredito.TIPO_VENCIMIENTO_UNICO
MODALIDAD_CUOTAS = MetodoPlazoCredito.TIPO_CUOTAS
CUENTA_ESTADOS_REPROGRAMABLES = (
    CuentaPorCobrar.ESTADO_ABIERTA,
    CuentaPorCobrar.ESTADO_PARCIAL,
    CuentaPorCobrar.ESTADO_VENCIDA,
)
CUOTA_ESTADOS_REPROGRAMABLES = (
    CuotaCxC.ESTADO_PENDIENTE,
    CuotaCxC.ESTADO_PARCIAL,
    CuotaCxC.ESTADO_VENCIDA,
)
PLAZO_CREDITO_DEFAULT_DIAS = 30


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
        'plazo_credito_dias': _plazo_credito_cliente(cliente),
        'saldo_pendiente': saldo,
        'credito_disponible': limite - saldo,
        'monto_vencido': vencido,
        'proximo_vencimiento': proxima.fecha_vencimiento if proxima else None,
    }


def _validar_cliente_credito(cliente):
    if cliente is None or not cliente.activo or cliente.es_contado:
        raise ClienteCreditoInvalidoError('La venta a credito requiere un cliente real activo.')


def _obtener_metodo(metodo_plazo_id, modalidad: str | None = None) -> MetodoPlazoCredito:
    if not metodo_plazo_id and modalidad == MODALIDAD_VENCIMIENTO_UNICO:
        metodo = (
            MetodoPlazoCredito.objects
            .filter(tipo=MetodoPlazoCredito.TIPO_VENCIMIENTO_UNICO, activo=True)
            .order_by('id')
            .first()
        )
        if metodo:
            return metodo

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


def _frecuencia_efectiva(credito_data: dict[str, Any], metodo: MetodoPlazoCredito) -> str:
    """Frecuencia de cuotas: la del payload si viene (editable por venta), si no la del metodo."""
    valor = (credito_data.get('frecuencia') or '').strip().upper()
    if not valor:
        return metodo.frecuencia
    if valor not in dict(MetodoPlazoCredito.FRECUENCIA_CHOICES):
        raise MetodoPlazoCreditoInvalidoError('Frecuencia de cuotas invalida.')
    return valor


def _modalidad_credito(credito_data: dict[str, Any], metodo: MetodoPlazoCredito | None = None) -> str:
    valor = (credito_data.get('modalidad') or '').strip().upper()
    if not valor:
        return metodo.tipo if metodo else MODALIDAD_VENCIMIENTO_UNICO
    if valor not in (MODALIDAD_VENCIMIENTO_UNICO, MODALIDAD_CUOTAS):
        raise MetodoPlazoCreditoInvalidoError('Modalidad de credito invalida.')
    if metodo and valor == MODALIDAD_VENCIMIENTO_UNICO and metodo.tipo != MetodoPlazoCredito.TIPO_VENCIMIENTO_UNICO:
        raise MetodoPlazoCreditoInvalidoError('La modalidad de vencimiento unico requiere un metodo de vencimiento unico.')
    if metodo and valor == MODALIDAD_CUOTAS and metodo.tipo != MetodoPlazoCredito.TIPO_CUOTAS:
        raise MetodoPlazoCreditoInvalidoError('La modalidad de cuotas requiere un metodo de cuotas.')
    return valor


def _plazo_credito_cliente(cliente) -> int:
    try:
        plazo = int(getattr(cliente, 'plazo_credito_dias', PLAZO_CREDITO_DEFAULT_DIAS) or PLAZO_CREDITO_DEFAULT_DIAS)
    except (TypeError, ValueError):
        plazo = PLAZO_CREDITO_DEFAULT_DIAS
    return min(max(plazo, 1), 365)


def _dias_entre_cuotas(metodo: MetodoPlazoCredito, frecuencia: str) -> int:
    if frecuencia == MetodoPlazoCredito.FRECUENCIA_SEMANAL:
        return 7
    if frecuencia == MetodoPlazoCredito.FRECUENCIA_QUINCENAL:
        return 15
    if frecuencia == MetodoPlazoCredito.FRECUENCIA_MENSUAL:
        return 30
    return max(int(metodo.dias_vencimiento), 1)


def _fechas_cuotas(
    *,
    metodo: MetodoPlazoCredito,
    cantidad_cuotas: int,
    fecha_primer_vencimiento: date | None,
    frecuencia: str | None = None,
) -> list[date]:
    primera = fecha_primer_vencimiento or (timezone.localdate() + timedelta(days=metodo.dias_vencimiento))
    if cantidad_cuotas == 1:
        return [primera]
    intervalo = _dias_entre_cuotas(metodo, frecuencia or metodo.frecuencia)
    return [primera + timedelta(days=intervalo * i) for i in range(cantidad_cuotas)]


def _interes_porcentaje(credito_data: dict[str, Any], metodo: MetodoPlazoCredito) -> Decimal:
    """Porcentaje de interes a aplicar: el del payload si viene, si no el del metodo."""
    valor = credito_data.get('interes_porcentaje')
    if valor in (None, ''):
        valor = metodo.interes_porcentaje
    try:
        porcentaje = _q(valor)
    except Exception:
        raise MetodoPlazoCreditoInvalidoError('Porcentaje de interes invalido.')
    if porcentaje < Decimal('0.00') or porcentaje > Decimal('100.00'):
        raise MetodoPlazoCreditoInvalidoError('El interes debe estar entre 0 y 100 por ciento.')
    return porcentaje


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

    modalidad_solicitada = _modalidad_credito(credito_data)
    metodo = _obtener_metodo(credito_data.get('metodo_plazo_id'), modalidad_solicitada)
    modalidad = _modalidad_credito(credito_data, metodo)
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

    interes_porcentaje = _interes_porcentaje(credito_data, metodo)
    # Interes flat sobre el capital financiado; venta.total y Pago(CREDITO)
    # siguen en capital, el cargo financiero vive solo en la CxC.
    monto_interes = _q(saldo_credito * (interes_porcentaje / Decimal('100')))
    saldo_financiado = _q(saldo_credito + monto_interes)

    saldo_actual = saldo_pendiente_cliente(cliente)
    limite = Decimal(str(cliente.limite_credito or 0))
    admin_override = _obtener_admin_override(credito_data.get('admin_override_id'))

    if saldo_actual + saldo_financiado > limite and admin_override is None:
        disponible = limite - saldo_actual
        raise LimiteCreditoExcedidoError(
            f'Limite de credito excedido. Disponible: ${disponible}, saldo nuevo: ${saldo_financiado}. '
            'Requiere autorizacion ADMIN/SYSADMIN.'
        )

    fecha_emision = timezone.localdate()
    if modalidad == MODALIDAD_VENCIMIENTO_UNICO:
        cantidad_cuotas = 1
        frecuencia = metodo.frecuencia
        fechas = [fecha_emision + timedelta(days=_plazo_credito_cliente(cliente))]
    else:
        cantidad_cuotas = metodo.normalizar_cantidad_cuotas(credito_data.get('cantidad_cuotas'))
        fecha_primer_vencimiento = _parse_date(credito_data.get('fecha_primer_vencimiento'))
        frecuencia = _frecuencia_efectiva(credito_data, metodo)
        fechas = _fechas_cuotas(
            metodo=metodo,
            cantidad_cuotas=cantidad_cuotas,
            fecha_primer_vencimiento=fecha_primer_vencimiento,
            frecuencia=frecuencia,
        )
    montos = _montos_cuotas(saldo_financiado, cantidad_cuotas)

    cuenta = CuentaPorCobrar.objects.create(
        cliente=cliente,
        venta=venta,
        metodo_plazo=metodo,
        total=total,
        monto_inicial=monto_inicial,
        saldo_original=saldo_credito,
        interes_porcentaje=interes_porcentaje,
        monto_interes=monto_interes,
        saldo=saldo_financiado,
        fecha_emision=fecha_emision,
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
                'saldo_nuevo': str(saldo_financiado),
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
            'capital': str(cuenta.saldo_original),
            'interes_porcentaje': str(cuenta.interes_porcentaje),
            'monto_interes': str(cuenta.monto_interes),
            'saldo': str(cuenta.saldo),
            'cuotas': cantidad_cuotas,
            'modalidad': modalidad,
            'plazo_credito_dias': _plazo_credito_cliente(cliente),
        },
        ip_address=ip_address,
        nivel_importancia=Auditoria.NivelImportancia.ALTA,
    )

    transaction.on_commit(lambda c=cuenta: sync_events.evento_cxc_creada(c))
    return cuenta


def reprogramar_cxc_por_plazo_cliente(
    cliente,
    usuario=None,
    origen: str = 'cliente_update',
    plazo_anterior: int | None = None,
    ip_address: str | None = None,
) -> dict:
    """Recalcula vencimientos de CxC abiertas con vencimiento unico.

    Las cuentas en cuotas conservan su calendario pactado. La reprogramacion
    cambia solo fechas y estados derivados; no modifica saldos, montos,
    intereses ni pagos aplicados.
    """
    nuevo_plazo = _plazo_credito_cliente(cliente)
    cambios = []

    with transaction.atomic():
        cuentas = (
            CuentaPorCobrar.objects
            .select_for_update()
            .select_related('metodo_plazo', 'venta')
            .filter(
                cliente=cliente,
                estado__in=CUENTA_ESTADOS_REPROGRAMABLES,
                metodo_plazo__tipo=MetodoPlazoCredito.TIPO_VENCIMIENTO_UNICO,
            )
            .order_by('id')
        )

        for cuenta in cuentas:
            fecha_anterior = cuenta.fecha_limite
            nueva_fecha = cuenta.fecha_emision + timedelta(days=nuevo_plazo)
            if fecha_anterior == nueva_fecha:
                continue

            estado_anterior = cuenta.estado
            cuenta.fecha_limite = nueva_fecha
            cuenta.recalcular_estado(guardar=False)
            cuenta.save(update_fields=['fecha_limite', 'estado', 'saldo', 'fecha_modificacion'])

            cuotas_actualizadas = 0
            for cuota in (
                cuenta.cuotas
                .select_for_update()
                .filter(estado__in=CUOTA_ESTADOS_REPROGRAMABLES)
                .order_by('numero')
            ):
                cuota.fecha_vencimiento = nueva_fecha
                cuota.recalcular_estado(guardar=False)
                cuota.save(update_fields=['fecha_vencimiento', 'estado', 'saldo', 'fecha_pago'])
                cuotas_actualizadas += 1

            cambios.append({
                'cuenta_id': cuenta.id,
                'venta': cuenta.venta.numero_venta if cuenta.venta_id else None,
                'fecha_anterior': fecha_anterior.isoformat() if fecha_anterior else None,
                'fecha_nueva': nueva_fecha.isoformat(),
                'estado_anterior': estado_anterior,
                'estado_nuevo': cuenta.estado,
                'cuotas_actualizadas': cuotas_actualizadas,
            })

        if cambios:
            Auditoria.registrar(
                accion=Auditoria.TipoAccion.EDITAR,
                descripcion=(
                    f'Reprogramacion CxC por plazo de cliente {cliente.nombre}: '
                    f'{len(cambios)} cuenta(s)'
                ),
                usuario=usuario,
                content_object=cliente,
                datos_anteriores={
                    'plazo_credito_dias': plazo_anterior,
                },
                datos_nuevos={
                    'plazo_credito_dias': nuevo_plazo,
                    'cuentas_afectadas': len(cambios),
                },
                metadata={
                    'origen': origen,
                    'cambios': cambios[:25],
                    'cambios_truncados': max(len(cambios) - 25, 0),
                },
                ip_address=ip_address,
                nivel_importancia=Auditoria.NivelImportancia.ALTA,
            )

    return {
        'cliente_id': cliente.id,
        'plazo_credito_dias': nuevo_plazo,
        'cuentas_afectadas': len(cambios),
        'cambios': cambios,
    }


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


def anular_pago_cxc_service(
    *,
    pago_id: int,
    usuario,
    motivo: str,
    ip_address: str | None = None,
) -> PagoCxC:
    """
    Reversa un abono CxC: restituye los saldos de las cuotas usando el JSON
    `aplicaciones` del pago y marca el pago como ANULADO.

    Politica: solo se puede anular el ULTIMO pago APLICADO de la cuenta (las
    aplicaciones a cuotas dependen del orden de los abonos; revertir LIFO es
    siempre consistente). Para revertir un pago anterior se anula en cadena.

    Caja: TurnoCaja.calcular_esperado() filtra por estado='APLICADO', asi que
    un turno abierto se ajusta solo. Si el turno donde se cobro ya cerro, la
    anulacion se permite igual (no se recalculan cierres historicos) y queda
    auditada con `turno_cerrado: true`.
    """
    motivo = (motivo or '').strip()
    if not motivo:
        raise MetodoPlazoCreditoInvalidoError('La anulacion de un abono requiere un motivo.')

    with transaction.atomic():
        try:
            pago = (
                PagoCxC.objects
                .select_for_update()
                .select_related('cuenta', 'cuenta__cliente', 'cuenta__venta')
                .get(id=pago_id)
            )
        except PagoCxC.DoesNotExist:
            raise MetodoPlazoCreditoInvalidoError('Abono CxC no encontrado.')

        if pago.estado != PagoCxC.ESTADO_APLICADO:
            raise MetodoPlazoCreditoInvalidoError('El abono ya esta anulado.')

        cuenta = (
            CuentaPorCobrar.objects
            .select_for_update()
            .get(id=pago.cuenta_id)
        )
        if cuenta.estado == CuentaPorCobrar.ESTADO_ANULADA:
            raise MetodoPlazoCreditoInvalidoError('La cuenta esta anulada; no se pueden revertir abonos.')

        hay_pago_posterior = (
            PagoCxC.objects
            .filter(cuenta=cuenta, estado=PagoCxC.ESTADO_APLICADO, id__gt=pago.id)
            .exists()
        )
        if hay_pago_posterior:
            raise MetodoPlazoCreditoInvalidoError(
                'Solo se puede anular el ultimo abono aplicado. Anula primero los abonos posteriores.'
            )

        cuotas_por_id = {
            c.id: c
            for c in cuenta.cuotas.select_for_update()
        }
        for aplicacion in pago.aplicaciones or []:
            cuota = cuotas_por_id.get(aplicacion.get('cuota_id'))
            if cuota is None:
                raise MetodoPlazoCreditoInvalidoError(
                    f'Cuota {aplicacion.get("numero")} del abono no existe; no se puede revertir.'
                )
            cuota.saldo = _q(cuota.saldo + Decimal(str(aplicacion.get('monto', '0'))))
            if cuota.saldo > cuota.monto:
                raise MetodoPlazoCreditoInvalidoError(
                    f'La reversa dejaria la cuota {cuota.numero} con saldo mayor a su monto.'
                )
            if cuota.saldo > Decimal('0.00'):
                # recalcular_estado no limpia fecha_pago al dejar de estar PAGADA
                cuota.fecha_pago = None
            cuota.recalcular_estado(guardar=True)

        cuenta.saldo = _q(cuenta.saldo + pago.monto)
        if cuenta.saldo > cuenta.monto_financiado:
            raise MetodoPlazoCreditoInvalidoError(
                'La reversa dejaria la cuenta con saldo mayor al financiado.'
            )
        cuenta.recalcular_estado(guardar=True)

        pago.estado = PagoCxC.ESTADO_ANULADO
        pago.anulado_por = usuario
        pago.fecha_anulacion = timezone.now()
        pago.motivo_anulacion = motivo
        pago.save(update_fields=['estado', 'anulado_por', 'fecha_anulacion', 'motivo_anulacion'])

        from apps.caja.models import TurnoCaja

        turno_cerrado = TurnoCaja.objects.filter(
            usuario=pago.registrado_por,
            estado='CERRADO',
            fecha_apertura__lte=pago.fecha_pago,
            fecha_cierre__gte=pago.fecha_pago,
        ).exists()

        Auditoria.registrar(
            accion=Auditoria.TipoAccion.EDITAR,
            descripcion=f'Abono CxC anulado para {cuenta.venta.numero_venta} - ${pago.monto}',
            usuario=usuario,
            content_object=pago,
            metadata={
                'cuenta_id': cuenta.id,
                'cliente': cuenta.cliente.nombre,
                'monto': str(pago.monto),
                'metodo': pago.metodo,
                'motivo': motivo,
                'saldo_restituido': str(cuenta.saldo),
                'turno_cerrado': turno_cerrado,
            },
            ip_address=ip_address,
            nivel_importancia=Auditoria.NivelImportancia.CRITICA,
        )

        transaction.on_commit(lambda p=pago: sync_events.evento_cxc_pago_anulado(p))

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
