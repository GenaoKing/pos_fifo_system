"""
apps/ventas/services/ventas_service.py

Servicio de procesamiento de venta. Encapsula toda la lógica de
negocio que antes vivía dentro del view `procesar_venta` de
apps/ventas/views.py.

Responsabilidades:
- Validar el payload (carrito, total, suma de pagos en mixto)
- Validar stock disponible según ConfiguracionNegocio
- Crear Venta + DetalleVenta + Pago en una sola transacción atómica
- Consumir stock vía FIFO
- Registrar auditoría DENTRO del atomic
- Disparar hooks DESPUÉS del commit:
    * sync engine (ya existente)
    * impresión térmica (ya extraída en Semana 0)
    * encolado de e-CF si modulo_ecf=True (NUEVO en Semana 3)

NO responsabilidades (las maneja el view):
- Parseo de JSON del request
- Construcción de JsonResponse
- Manejo de status HTTP
- Validación de rol del usuario (decorador @login_required basta;
  los permisos por rol se validan en endpoints de admin como anular)

Patrón de uso desde el view:
    try:
        venta = procesar_venta_service(
            usuario=request.user,
            datos=json.loads(request.body),
        )
        return JsonResponse({'success': True, 'venta': {...}})
    except ErrorVentaBase as exc:
        return JsonResponse(
            {'success': False, 'error': str(exc)},
            status=exc.status_code,
        )
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.db import models, transaction
from django.shortcuts import get_object_or_404

from apps.auditoria.models import Auditoria
from apps.configuracion.utils import get_config, modulo_activo
from apps.inventario.fifo_logic import procesar_venta_fifo
from apps.inventario.models import Lote
from apps.productos.models import Producto
from apps.sync import events as sync_events
from utils.impresoras.manager import print_manager

from ..models import DetalleVenta, Pago, Venta
from .exceptions import (
    CarritoVacioError,
    ClienteCreditoInvalidoError,
    MetodoPlazoCreditoInvalidoError,
    ModuloInactivoError,
    PagoMixtoInconsistenteError,
    ProductoInexistenteError,
    StockInsuficienteError,
    TipoECFInvalidoError,
    TotalInconsistenteError,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

logger = logging.getLogger('ventas.service')


# =============================================================================
# Tipos de e-CF aceptados desde el POS
# =============================================================================
# El cajero elige uno de estos antes de cerrar venta. Default '32' (consumo).
# El tipo 34 (NC) NO se elige desde el POS: lo dispara la anulación.
TIPOS_ECF_POS = ('31', '32')
TIPO_ECF_DEFAULT = '32'


# =============================================================================
# Punto de entrada principal
# =============================================================================

def procesar_venta_service(
    *,
    usuario: 'AbstractUser',
    datos: dict[str, Any],
    ip_address: str | None = None,
) -> Venta:
    """
    Procesa una venta completa desde datos parseados del POS.

    Args:
        usuario: usuario autenticado que está cerrando la venta.
        datos: dict ya parseado del JSON del request. Forma esperada:
            {
                'carrito': [
                    {'id': int, 'cantidad': int, 'precio_venta': str|float,
                     'descuento': str|float (opcional)},
                    ...
                ],
                'metodo_pago': 'efectivo' | 'transferencia' | 'tarjeta' | 'mixto' | 'credito',
                'monto_efectivo': str|float (opcional),
                'monto_transferencia': str|float (opcional),
                'monto_tarjeta': str|float (opcional),
                'referencia_tarjeta': str (opcional),
                'total': str|float,
                'cliente_id': int (opcional),
                'tipo_ecf': '31' | '32' (opcional, default '32'),
            }
        ip_address: IP del cliente, para auditoría. None si no se pudo extraer.

    Returns:
        Instancia Venta persistida y commiteada.

    Raises:
        ErrorVentaBase y subclases ante fallos de validación o negocio.
        Cualquier otra excepción propaga (el view la captura como 500).
    """
    # ----------------------- Parseo y validación de tipos primitivos
    carrito = datos.get('carrito') or []
    metodo_pago = datos.get('metodo_pago', 'efectivo')
    es_credito = metodo_pago == 'credito'
    credito_data = datos.get('credito') or {}
    monto_efectivo = _decimal(datos.get('monto_efectivo', 0))
    monto_transferencia = _decimal(datos.get('monto_transferencia', 0))
    monto_tarjeta = _decimal(datos.get('monto_tarjeta', 0))
    referencia_tarjeta = (datos.get('referencia_tarjeta') or '').strip()
    total_esperado = _decimal(datos.get('total', 0))
    cliente_id = datos.get('cliente_id')
    tipo_ecf = (datos.get('tipo_ecf') or TIPO_ECF_DEFAULT).strip()

    # ----------------------- Validaciones pre-transacción (lectura pura)
    if not carrito:
        raise CarritoVacioError('El carrito está vacío.')

    if tipo_ecf not in TIPOS_ECF_POS:
        raise TipoECFInvalidoError(
            f'Tipo de e-CF "{tipo_ecf}" no soportado desde el POS. '
            f'Válidos: {", ".join(TIPOS_ECF_POS)}.'
        )

    if metodo_pago == 'mixto':
        suma_pagos = monto_efectivo + monto_transferencia + monto_tarjeta
        if abs(suma_pagos - total_esperado) > Decimal('0.01'):
            raise PagoMixtoInconsistenteError(
                f'La suma de los pagos no coincide con el total. '
                f'Total: ${total_esperado}, suma: ${suma_pagos} '
                f'(efectivo: ${monto_efectivo}, '
                f'transferencia: ${monto_transferencia}, '
                f'tarjeta: ${monto_tarjeta}).'
            )
    elif es_credito:
        monto_inicial = _decimal(credito_data.get('monto_inicial', 0))
        metodo_inicial = (credito_data.get('metodo_inicial') or 'efectivo').strip()
        if monto_inicial <= 0:
            pass
        elif metodo_inicial == 'mixto':
            suma_inicial = (
                _decimal(credito_data.get('monto_efectivo', monto_efectivo))
                + _decimal(credito_data.get('monto_transferencia', monto_transferencia))
                + _decimal(credito_data.get('monto_tarjeta', monto_tarjeta))
            )
            if abs(suma_inicial - monto_inicial) > Decimal('0.01'):
                raise PagoMixtoInconsistenteError(
                    f'La suma del inicial no coincide. '
                    f'Inicial: ${monto_inicial}, suma: ${suma_inicial}.'
                )
        elif metodo_inicial not in ('efectivo', 'transferencia', 'tarjeta'):
            raise MetodoPlazoCreditoInvalidoError('Metodo de inicial para credito invalido.')

    # El modulo de cuentas por cobrar debe estar activo para vender a credito.
    # Falla rapido antes de tocar inventario/transaccion. La UI del POS no debe
    # ofrecer credito si el modulo esta off; este es el guard de servidor.
    if es_credito and not modulo_activo('cuentas_por_cobrar'):
        raise ModuloInactivoError(
            'El modulo de cuentas por cobrar no esta incluido en el plan del negocio; '
            'no se puede registrar una venta a credito.'
        )

    config = get_config()

    # ----------------------- Transacción atómica
    with transaction.atomic():
        _validar_stock(carrito, permitir_negativo=config.permitir_inventario_negativo)

        venta = _crear_venta(
            usuario=usuario,
            carrito=carrito,
            cliente_id=cliente_id,
            total_esperado=total_esperado,
            condicion_pago='CREDITO' if es_credito else 'CONTADO',
        )

        _crear_detalles_y_consumir_fifo(
            venta=venta,
            carrito=carrito,
            usuario=usuario,
        )

        _registrar_pagos(
            venta=venta,
            metodo_pago=metodo_pago,
            monto_efectivo=monto_efectivo,
            monto_transferencia=monto_transferencia,
            monto_tarjeta=monto_tarjeta,
            referencia_tarjeta=referencia_tarjeta,
            credito_data=credito_data,
        )

        if es_credito:
            from apps.cuentas_por_cobrar.services import crear_cuenta_para_venta

            crear_cuenta_para_venta(
                venta=venta,
                usuario=usuario,
                credito_data=credito_data,
                ip_address=ip_address,
            )

        # Auditoría dentro del atomic (atómica con la venta)
        Auditoria.registrar_venta(
            venta=venta,
            usuario=usuario,
            ip_address=ip_address,
        )

        # ------------------ Hooks post-commit
        # Sync engine (existente)
        transaction.on_commit(lambda v=venta: sync_events.evento_venta_creada(v))

        # Impresión fuera de la transacción: si la térmica falla, no
        # se hace rollback de la venta. Mismo patrón que sync.
        transaction.on_commit(
            lambda v=venta, u=usuario: print_manager.print_ticket_venta(
                venta=v,
                usuario=u,
                reimpresion=False,
            )
        )

        # Encolado de e-CF (Semana 3): solo si el módulo está activo.
        # Crea ECF en estado PENDIENTE; el management command
        # ecf_procesar_pendientes lo levanta y lo emite contra MSeller.
        # Esto desacopla el flujo de venta del flujo fiscal: la cajera
        # no espera por DGII.
        if modulo_activo('ecf'):
            transaction.on_commit(
                lambda v=venta, t=tipo_ecf: _hook_encolar_ecf(v, t)
            )

    return venta


# =============================================================================
# Helpers internos
# =============================================================================

def _decimal(valor: Any) -> Decimal:
    """Conversión defensiva a Decimal vía str para no perder precisión."""
    return Decimal(str(valor or 0))


def _validar_stock(carrito: list[dict], *, permitir_negativo: bool) -> None:
    """
    Verifica stock disponible para cada item del carrito.

    Si la configuración no permite inventario negativo y un producto
    no tiene stock suficiente, levanta StockInsuficienteError con el
    nombre del producto y la cantidad disponible.
    """
    if permitir_negativo:
        return

    for item in carrito:
        producto_id = item['id']
        cantidad_solicitada = item['cantidad']

        try:
            producto = Producto.objects.get(id=producto_id)
        except Producto.DoesNotExist:
            raise ProductoInexistenteError(
                f'El producto con id={producto_id} no existe.'
            )

        stock_disponible = Lote.objects.filter(
            producto=producto,
            cantidad_actual__gt=0,
            activo=True,
        ).aggregate(total=models.Sum('cantidad_actual'))['total'] or 0

        if cantidad_solicitada > stock_disponible:
            raise StockInsuficienteError(
                f'Stock insuficiente: {producto.nombre}. '
                f'Disponible: {stock_disponible}, solicitado: {cantidad_solicitada}.'
            )


def _crear_venta(
    *,
    usuario: 'AbstractUser',
    carrito: list[dict],
    cliente_id: int | None,
    total_esperado: Decimal,
    condicion_pago: str,
) -> Venta:
    """
    Crea la cabecera Venta. Calcula totales desde el carrito y los
    valida contra `total_esperado`. El número de venta lo asigna el
    propio modelo en su save().

    Asigna cliente al crear (no en un segundo save) para evitar
    el patrón de doble persistencia que tenía el view original.
    """
    subtotal = sum(
        _decimal(item['cantidad']) * _decimal(item['precio_venta'])
        for item in carrito
    )
    descuento_total = sum(_decimal(item.get('descuento', 0)) for item in carrito)
    total = subtotal - descuento_total

    if abs(total - total_esperado) > Decimal('0.01'):
        raise TotalInconsistenteError(
            f'Total no coincide. Esperado: ${total_esperado}, '
            f'calculado desde carrito: ${total}.'
        )

    cliente = None
    if cliente_id:
        from apps.clientes.models import Cliente
        try:
            cliente = Cliente.objects.get(id=cliente_id, activo=True)
        except Cliente.DoesNotExist:
            # Comportamiento legacy: si el cliente no existe, queda
            # como contado (null). Se mantiene para no romper UI.
            cliente = None

    if condicion_pago == 'CREDITO':
        if cliente is None or not cliente.activo or cliente.es_contado:
            raise ClienteCreditoInvalidoError(
                'La venta a credito requiere un cliente real activo.'
            )

    return Venta.objects.create(
        usuario=usuario,
        cliente=cliente,
        subtotal=subtotal,
        descuento_total=descuento_total,
        total=total,
        condicion_pago=condicion_pago,
        estado='COMPLETADA',
    )


def _crear_detalles_y_consumir_fifo(
    *,
    venta: Venta,
    carrito: list[dict],
    usuario: 'AbstractUser',
) -> None:
    """
    Crea DetalleVenta por cada item del carrito y consume stock FIFO.

    DetalleVenta autocalcula subtotal/total_linea/descuento_porcentaje
    en su save(); solo pasamos los campos primarios.
    """
    for item in carrito:
        try:
            producto = Producto.objects.get(id=item['id'])
        except Producto.DoesNotExist:
            raise ProductoInexistenteError(
                f'El producto con id={item["id"]} no existe.'
            )

        DetalleVenta.objects.create(
            venta=venta,
            producto=producto,
            cantidad=item['cantidad'],
            precio_unitario=_decimal(item['precio_venta']),
            descuento_monto=_decimal(item.get('descuento', 0)),
        )

        # Consumir stock FIFO. La función retorna dict con success y
        # cantidad_faltante. Si permite_inventario_negativo=True,
        # success puede ser True con cantidad_faltante > 0.
        resultado = procesar_venta_fifo(
            producto_id=producto.id,
            cantidad_solicitada=item['cantidad'],
            venta_id=venta.id,
            usuario=usuario,
        )
        if not resultado['success']:
            logger.warning(
                f'FIFO falló para venta={venta.numero_venta} '
                f'producto={producto.nombre}: {resultado.get("error")}'
            )
        if resultado.get('cantidad_faltante', 0) > 0:
            logger.warning(
                f'Stock insuficiente al consumir FIFO: '
                f'venta={venta.numero_venta} producto={producto.nombre} '
                f'vendido={resultado.get("cantidad_vendida")} '
                f'faltante={resultado.get("cantidad_faltante")}'
            )


def _registrar_pagos(
    *,
    venta: Venta,
    metodo_pago: str,
    monto_efectivo: Decimal,
    monto_transferencia: Decimal,
    monto_tarjeta: Decimal,
    referencia_tarjeta: str,
    credito_data: dict[str, Any] | None = None,
) -> None:
    """
    Persiste los Pago de la venta. Para métodos puros, un solo Pago
    con monto = total. Para mixto, un Pago por método con monto > 0.
    """
    numero = venta.numero_venta
    total = venta.total
    credito_data = credito_data or {}

    if metodo_pago == 'efectivo':
        Pago.objects.create(
            venta=venta,
            metodo='EFECTIVO',
            monto=total,
            referencia=f'Efectivo - {numero}',
        )
    elif metodo_pago == 'transferencia':
        Pago.objects.create(
            venta=venta,
            metodo='TRANSFERENCIA',
            monto=total,
            referencia=f'Transferencia - {numero}',
        )
    elif metodo_pago == 'tarjeta':
        Pago.objects.create(
            venta=venta,
            metodo='TARJETA',
            monto=total,
            referencia=f'Tarjeta {referencia_tarjeta} - {numero}',
        )
    elif metodo_pago == 'mixto':
        if monto_efectivo > 0:
            Pago.objects.create(
                venta=venta,
                metodo='EFECTIVO',
                monto=monto_efectivo,
                referencia=f'Efectivo (Mixto) - {numero}',
            )
        if monto_transferencia > 0:
            Pago.objects.create(
                venta=venta,
                metodo='TRANSFERENCIA',
                monto=monto_transferencia,
                referencia=f'Transferencia (Mixto) - {numero}',
            )
        if monto_tarjeta > 0:
            Pago.objects.create(
                venta=venta,
                metodo='TARJETA',
                monto=monto_tarjeta,
                referencia=f'Tarjeta (Mixto) {referencia_tarjeta} - {numero}',
            )
    elif metodo_pago == 'credito':
        monto_inicial = _decimal(credito_data.get('monto_inicial', 0))
        saldo_credito = total - monto_inicial
        metodo_inicial = (credito_data.get('metodo_inicial') or 'efectivo').strip()

        if monto_inicial > 0:
            if metodo_inicial == 'efectivo':
                Pago.objects.create(
                    venta=venta,
                    metodo='EFECTIVO',
                    monto=monto_inicial,
                    referencia=f'Inicial credito efectivo - {numero}',
                )
            elif metodo_inicial == 'transferencia':
                Pago.objects.create(
                    venta=venta,
                    metodo='TRANSFERENCIA',
                    monto=monto_inicial,
                    referencia=f'Inicial credito transferencia - {numero}',
                )
            elif metodo_inicial == 'tarjeta':
                Pago.objects.create(
                    venta=venta,
                    metodo='TARJETA',
                    monto=monto_inicial,
                    referencia=f'Inicial credito tarjeta {referencia_tarjeta} - {numero}',
                )
            elif metodo_inicial == 'mixto':
                inicial_efectivo = _decimal(credito_data.get('monto_efectivo', 0))
                inicial_transferencia = _decimal(credito_data.get('monto_transferencia', 0))
                inicial_tarjeta = _decimal(credito_data.get('monto_tarjeta', 0))
                if inicial_efectivo > 0:
                    Pago.objects.create(
                        venta=venta,
                        metodo='EFECTIVO',
                        monto=inicial_efectivo,
                        referencia=f'Inicial credito efectivo mixto - {numero}',
                    )
                if inicial_transferencia > 0:
                    Pago.objects.create(
                        venta=venta,
                        metodo='TRANSFERENCIA',
                        monto=inicial_transferencia,
                        referencia=f'Inicial credito transferencia mixto - {numero}',
                    )
                if inicial_tarjeta > 0:
                    Pago.objects.create(
                        venta=venta,
                        metodo='TARJETA',
                        monto=inicial_tarjeta,
                        referencia=f'Inicial credito tarjeta mixto {referencia_tarjeta} - {numero}',
                    )

        if saldo_credito > 0:
            Pago.objects.create(
                venta=venta,
                metodo='CREDITO',
                monto=saldo_credito,
                referencia=f'CxC - {numero}',
            )


def _hook_encolar_ecf(venta: Venta, tipo_ecf: str) -> None:
    """
    Hook que corre post-commit. Crea el ECF en estado PENDIENTE y
    registra el evento inicial. NO llama a MSeller — eso lo hace el
    management command ecf_procesar_pendientes.

    Si algo falla aquí, NO se hace rollback de la venta (estamos
    post-commit). Se loguea el error para que el operador lo investigue.
    """
    try:
        from apps.facturacion_electronica.services.cola_emision import (
            encolar_emision,
        )
        encolar_emision(venta=venta, tipo_ecf=tipo_ecf)
    except Exception as exc:
        # Logging detallado pero no relanzamos: la venta ya commiteó.
        # El ECF se puede crear manualmente o por otro mecanismo si
        # el operador lo nota.
        logger.exception(
            f'Falló encolar e-CF para venta={venta.numero_venta} '
            f'tipo={tipo_ecf}: {exc}'
        )
