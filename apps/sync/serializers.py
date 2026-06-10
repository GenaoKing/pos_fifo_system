"""
apps/sync/serializers.py

Serializacion de objetos Django a dict (JSON-ready) para enviar al cloud.

Decision de diseno: usamos funciones simples en vez de DRF serializers porque
los payloads de sync son DENSOS (objeto + todos sus children inline) y no
necesitan validacion de entrada (solo generamos, no recibimos). DRF anade
complejidad sin aportar nada en este caso.

Reglas:
- Decimals -> str (preserva precision)
- Datetimes -> isoformat() con tzinfo (awareness gracias a USE_TZ=True)
- Ninguna FK cruda: siempre el identificador natural ('sku', 'codigo', 'username')
  para que el cloud pueda resolver sin depender del ID local.
"""
from decimal import Decimal


def _d(value):
    """Decimal -> str. Util para no perder precision en JSON."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    return str(Decimal(str(value)))


def _dt(value):
    """Datetime -> isoformat (o None)."""
    return value.isoformat() if value else None


# ============================================================================
# VENTAS
# ============================================================================

def serializar_venta(venta):
    """Serializa una Venta con todos sus detalles y pagos."""
    return {
        'numero_venta': venta.numero_venta,
        'sucursal_codigo': venta.sucursal.codigo if venta.sucursal_id else None,
        'fecha_venta': _dt(venta.fecha_venta),
        'usuario_username': venta.usuario.username if venta.usuario_id else None,
        'cliente_cedula_rnc': (
            venta.cliente.cedula_rnc if venta.cliente_id and venta.cliente.cedula_rnc else None
        ),
        'cliente_nombre': venta.cliente.nombre if venta.cliente_id else None,
        'subtotal': _d(venta.subtotal),
        'descuento_total': _d(venta.descuento_total),
        'total': _d(venta.total),
        'estado': venta.estado,
        'condicion_pago': getattr(venta, 'condicion_pago', 'CONTADO'),
        'notas': venta.notas or '',
        'detalles': [_serializar_detalle(d) for d in venta.detalles.all()],
        'pagos': [_serializar_pago(p) for p in venta.pagos.all()],
        'cuenta_por_cobrar': _serializar_cuenta_cxc(getattr(venta, 'cuenta_por_cobrar', None)),
    }


def _serializar_detalle(detalle):
    """Una linea de venta."""
    return {
        'producto_sku': detalle.producto.sku if detalle.producto_id else None,
        'producto_nombre': detalle.producto.nombre if detalle.producto_id else '',
        'cantidad': _d(detalle.cantidad),
        'precio_unitario': _d(detalle.precio_unitario),
        'subtotal': _d(detalle.subtotal),
        'descuento_monto': _d(detalle.descuento_monto),
        'descuento_porcentaje': _d(detalle.descuento_porcentaje),
        'total_linea': _d(detalle.total_linea),
        'costo_fifo': _d(detalle.costo_fifo),
    }


def _serializar_pago(pago):
    """Un pago (puede haber varios por venta en pago mixto)."""
    return {
        'metodo': pago.metodo,
        'monto': _d(pago.monto),
        'referencia': pago.referencia or '',
        'fecha_pago': _dt(pago.fecha_pago),
    }


def serializar_anulacion_venta(venta):
    """
    Payload de anulacion. No reenviamos los detalles completos (ya estan en el
    cloud via VENTA_CREADA previo): solo la referencia y los datos de anulacion.
    """
    return {
        'numero_venta': venta.numero_venta,
        'sucursal_codigo': venta.sucursal.codigo if venta.sucursal_id else None,
        'estado': venta.estado,
        'fecha_anulacion': _dt(venta.fecha_anulacion),
        'anulada_por_username': (
            venta.anulada_por.username if venta.anulada_por_id else None
        ),
        'motivo_anulacion': venta.motivo_anulacion or '',
    }


def _serializar_cuenta_cxc(cuenta):
    if cuenta is None:
        return None
    return {
        'cuenta_id_local': cuenta.id,
        'metodo_plazo': cuenta.metodo_plazo.nombre,
        'total': _d(cuenta.total),
        'monto_inicial': _d(cuenta.monto_inicial),
        'saldo': _d(cuenta.saldo),
        'estado': cuenta.estado,
        'fecha_limite': cuenta.fecha_limite.isoformat() if cuenta.fecha_limite else None,
        'cuotas': [
            {
                'numero': c.numero,
                'monto': _d(c.monto),
                'saldo': _d(c.saldo),
                'estado': c.estado,
                'fecha_vencimiento': c.fecha_vencimiento.isoformat(),
            }
            for c in cuenta.cuotas.all()
        ],
    }


def serializar_cxc(cuenta):
    return {
        'cuenta_id_local': cuenta.id,
        'numero_venta': cuenta.venta.numero_venta,
        'sucursal_codigo': cuenta.sucursal.codigo if cuenta.sucursal_id else None,
        'cliente_cedula_rnc': cuenta.cliente.cedula_rnc or None,
        'cliente_nombre': cuenta.cliente.nombre,
        'metodo_plazo': cuenta.metodo_plazo.nombre,
        'total': _d(cuenta.total),
        'monto_inicial': _d(cuenta.monto_inicial),
        'saldo_original': _d(cuenta.saldo_original),
        'interes_porcentaje': _d(cuenta.interes_porcentaje),
        'monto_interes': _d(cuenta.monto_interes),
        'saldo': _d(cuenta.saldo),
        'estado': cuenta.estado,
        'fecha_emision': cuenta.fecha_emision.isoformat(),
        'fecha_limite': cuenta.fecha_limite.isoformat(),
        'override_autorizado_por_username': (
            cuenta.override_autorizado_por.username
            if cuenta.override_autorizado_por_id else None
        ),
        'cuotas': [
            {
                'numero': c.numero,
                'monto': _d(c.monto),
                'saldo': _d(c.saldo),
                'fecha_vencimiento': c.fecha_vencimiento.isoformat(),
                'estado': c.estado,
            }
            for c in cuenta.cuotas.all()
        ],
    }


def serializar_pago_cxc(pago):
    cuenta = pago.cuenta
    return {
        'pago_id_local': pago.id,
        'cuenta_id_local': cuenta.id,
        'numero_venta': cuenta.venta.numero_venta,
        'sucursal_codigo': cuenta.sucursal.codigo if cuenta.sucursal_id else None,
        'cliente_cedula_rnc': cuenta.cliente.cedula_rnc or None,
        'metodo': pago.metodo,
        'monto': _d(pago.monto),
        'referencia': pago.referencia or '',
        'fecha_pago': _dt(pago.fecha_pago),
        'registrado_por_username': pago.registrado_por.username if pago.registrado_por_id else None,
        'estado': pago.estado,
        'aplicaciones': pago.aplicaciones or [],
        'saldo_cuenta': _d(cuenta.saldo),
    }


def serializar_anulacion_pago_cxc(pago):
    """
    Payload de reversa de un abono CxC.

    Incluye `fecha_pago` y `monto` originales para que el cloud localice el
    pago con el mismo matching que CXC_PAGO_REGISTRADO, mas un snapshot
    post-reversa de la cuenta y sus cuotas para reponer estados sin recalcular.
    """
    cuenta = pago.cuenta
    return {
        'pago_id_local': pago.id,
        'cuenta_id_local': cuenta.id,
        'numero_venta': cuenta.venta.numero_venta,
        'sucursal_codigo': cuenta.sucursal.codigo if cuenta.sucursal_id else None,
        'metodo': pago.metodo,
        'monto': _d(pago.monto),
        'fecha_pago': _dt(pago.fecha_pago),
        'motivo_anulacion': pago.motivo_anulacion or '',
        'anulado_por_username': pago.anulado_por.username if pago.anulado_por_id else None,
        'fecha_anulacion': _dt(pago.fecha_anulacion),
        'saldo_cuenta': _d(cuenta.saldo),
        'estado_cuenta': cuenta.estado,
        'cuotas': [
            {
                'numero': c.numero,
                'saldo': _d(c.saldo),
                'estado': c.estado,
            }
            for c in cuenta.cuotas.all()
        ],
    }


# ============================================================================
# CAJA
# ============================================================================

def serializar_apertura_caja(turno):
    """
    Payload de apertura de turno.

    Incluye todos los datos necesarios para que el cloud cree el TurnoCaja
    con estado='ABIERTO'. Los movimientos y cierre se enviaran despues con
    sus propios eventos.
    """
    return {
        'turno_id_local': turno.id,
        'sucursal_codigo': (
            turno.caja.sucursal.codigo if turno.caja.sucursal_id else None
        ),
        'caja_nombre': turno.caja.nombre,
        'usuario_username': turno.usuario.username if turno.usuario_id else None,
        'fecha_apertura': _dt(turno.fecha_apertura),
        'fondo_apertura': _d(turno.fondo_apertura),
        'notas_apertura': getattr(turno, 'notas_apertura', '') or '',
    }


def serializar_movimiento_caja(movimiento):
    """
    Payload de movimiento de caja (RETIRO, GASTO, INGRESO).

    Importante: incluye referencia al turno via (sucursal, caja, fecha_apertura)
    porque el turno_id_local no necesariamente coincide entre sucursal y cloud.
    El handler cloud busca el turno por esta tripleta.
    """
    turno = movimiento.turno
    return {
        'movimiento_id_local': movimiento.id,
        'sucursal_codigo': (
            turno.caja.sucursal.codigo if turno.caja.sucursal_id else None
        ),
        'caja_nombre': turno.caja.nombre,
        'turno_fecha_apertura': _dt(turno.fecha_apertura),
        'tipo': movimiento.tipo,
        'monto': _d(movimiento.monto),
        'descripcion': movimiento.descripcion or '',
        'registrado_por_username': (
            movimiento.registrado_por.username if movimiento.registrado_por_id else None
        ),
        'autorizado_por_username': (
            movimiento.autorizado_por.username
            if getattr(movimiento, 'autorizado_por_id', None) else None
        ),
        'fecha': _dt(movimiento.fecha),
    }


def serializar_cierre_caja(turno):
    """
    Payload de cierre de turno.

    En el cloud, este evento actualiza el TurnoCaja que ya fue creado por
    APERTURA_CAJA (en Opcion 3 del diseno). Si por alguna razon el turno
    no existe, el cloud lo creara como fallback.
    """
    return {
        'turno_id_local': turno.id,
        'sucursal_codigo': (
            turno.caja.sucursal.codigo if turno.caja.sucursal_id else None
        ),
        'caja_nombre': turno.caja.nombre,
        'usuario_username': turno.usuario.username if turno.usuario_id else None,
        'fecha_apertura': _dt(turno.fecha_apertura),
        'fecha_cierre': _dt(turno.fecha_cierre),
        'fondo_apertura': _d(turno.fondo_apertura),
        'monto_contado': _d(turno.monto_contado),
        'monto_esperado': _d(getattr(turno, 'monto_esperado', None)),
        'diferencia': _d(getattr(turno, 'diferencia', None)),
        'cerrado_por_username': (
            turno.cerrado_por.username if getattr(turno, 'cerrado_por_id', None) else None
        ),
        'notas_cierre': getattr(turno, 'notas_cierre', '') or '',
    }


# ============================================================================
# INVENTARIO / COMPRAS
# ============================================================================

def serializar_ajuste_inventario(ajuste):
    """Serializa un AjusteInventario (merma, dano, correccion)."""
    return {
        'ajuste_id_local': ajuste.id,
        'sucursal_codigo': 'LOCAL',
        'producto_sku': ajuste.producto.sku if getattr(ajuste, 'producto_id', None) else None,
        'tipo': ajuste.tipo,
        'cantidad': _d(ajuste.cantidad),
        'motivo': getattr(ajuste, 'motivo', '') or '',
        'usuario_username': (
            ajuste.usuario.username if getattr(ajuste, 'usuario_id', None) else None
        ),
        'fecha': _dt(getattr(ajuste, 'fecha', None) or getattr(ajuste, 'created_at', None)),
    }


def serializar_compra(compra):
    """Serializa una Compra con sus detalles."""
    return {
        'compra_id_local': compra.id,
        'sucursal_codigo': 'LOCAL',
        'numero_compra': getattr(compra, 'numero_compra', '') or '',
        'proveedor': str(getattr(compra, 'proveedor', '') or ''),
        'fecha_compra': _dt(getattr(compra, 'fecha_compra', None)),
        'total': _d(getattr(compra, 'total', 0)),
        'detalles': [
            {
                'producto_sku': d.producto.sku if d.producto_id else None,
                'cantidad': _d(d.cantidad),
                'costo_unitario': _d(d.costo_unitario),
                'subtotal': _d(getattr(d, 'subtotal', None) or d.cantidad * d.costo_unitario),
            }
            for d in compra.detalles.all()
        ] if hasattr(compra, 'detalles') else [],
    }
