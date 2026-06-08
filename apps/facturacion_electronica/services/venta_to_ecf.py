"""
apps/facturacion_electronica/services/venta_to_ecf.py

Mapper neutro: Venta → dict ecf_data.

Este módulo NO sabe nada de MSeller. Convierte una Venta del POS al
formato de diccionario que define la interfaz EmisorECFInterface.
Cualquier proveedor (MSeller hoy, nativa en Fase 2) consume este
mismo dict y lo transforma a su formato específico.

La lógica fiscal vive acá:
- Desglose de ITBIS según ConfiguracionNegocio (incluido vs sumado)
- Determinación de IndicadorFacturacion por línea (1=18%, 2=16%, 4=exento)
- Cálculo de totales gravados/exentos
- Manejo de descuentos por línea

NO maneja:
- Tipo de e-CF (lo decide el cajero, se pasa como parámetro)
- Asignación de eNCF (lo asigna el proveedor)
- Firma, envío, persistencia (todo eso es responsabilidad de capas superiores)
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from apps.configuracion.utils import get_config

if TYPE_CHECKING:
    from apps.productos.models import Producto
    from apps.ventas.models import DetalleVenta, Venta


# =============================================================================
# Constantes y helpers de redondeo
# =============================================================================

# DGII espera 2 decimales en montos. Redondeo half-up es el estándar
# fiscal (no el "banker's rounding" de Python). Cuidar esto evita
# rechazos por totales que difieren en 0.01 del cálculo de DGII.
DOS_DECIMALES = Decimal('0.01')


def _q(value: Decimal | float | int | str) -> Decimal:
    """Cuantiza a 2 decimales con redondeo half-up. Único punto de redondeo."""
    return Decimal(str(value)).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)


# =============================================================================
# Resolución de ITBIS por producto
# =============================================================================
# Centralizado acá para que el día que Producto tenga su propio campo
# `itbis_pct`, solo cambie esta función. El resto del mapper no se entera.

def _get_itbis_pct(producto: 'Producto', config) -> Decimal:
    """
    Retorna el porcentaje de ITBIS aplicable a un producto.

    Hoy: usa el global de ConfiguracionNegocio.
    Futuro: cuando Producto.itbis_pct exista, leerlo y caer al global
            si es None.
    """
    # TODO: cuando se agregue Producto.itbis_pct (migración futura),
    # cambiar a: return producto.itbis_pct or config.itbis_porcentaje_global
    return Decimal(str(config.itbis_porcentaje_global))


# =============================================================================
# Indicador de facturación DGII
# =============================================================================
# 1 = gravado al 18%, 2 = gravado al 16%, 4 = exento
# La doc DGII define más códigos pero el POS solo emite estos tres.

def _indicador_facturacion(itbis_pct: Decimal) -> int:
    if itbis_pct == Decimal('18') or itbis_pct == Decimal('18.00'):
        return 1
    if itbis_pct == Decimal('16') or itbis_pct == Decimal('16.00'):
        return 2
    if itbis_pct == 0:
        return 4
    # Tasa no estándar: lo tratamos como gravado al 18% por defecto
    # y emitimos warning. En la práctica DR esto no debería pasar.
    import logging
    logging.getLogger('ecf.mapper').warning(
        f'ITBIS pct no estándar: {itbis_pct}. Tratando como 18%.'
    )
    return 1


# =============================================================================
# Cálculo de base e ITBIS por línea
# =============================================================================

@dataclass
class LineaCalculada:
    """Resultado del desglose fiscal de una línea de venta."""
    numero_linea: int
    indicador_facturacion: int
    nombre_item: str
    cantidad: Decimal
    precio_unitario_base: Decimal      # sin ITBIS, lo que va en PrecioUnitarioItem
    descuento_monto: Decimal           # sin ITBIS, sobre la base
    monto_item: Decimal                # base * cantidad - descuento (sin ITBIS)
    itbis_linea: Decimal               # ITBIS resultante
    itbis_pct: Decimal                 # 18, 16, 0


def _calcular_linea(
    detalle: 'DetalleVenta',
    numero_linea: int,
    config,
) -> LineaCalculada:
    """
    Convierte un DetalleVenta del POS en su descomposición fiscal.

    Maneja los dos modos de configuración:
    - itbis_incluido_en_precio=True:  precio_unitario YA tiene ITBIS,
      se desglosa con base = precio / (1 + pct/100).
    - itbis_incluido_en_precio=False: precio_unitario es base, ITBIS
      se calcula sumando.
    """
    producto = detalle.producto
    cantidad = Decimal(str(detalle.cantidad))
    precio_unit_pos = Decimal(str(detalle.precio_unitario))
    descuento_pos = Decimal(str(detalle.descuento_monto or 0))

    itbis_pct = _get_itbis_pct(producto, config)
    factor = Decimal('1') + (itbis_pct / Decimal('100'))

    if config.itbis_incluido_en_precio and itbis_pct > 0:
        # Back-calculo: el precio del POS incluye ITBIS, hay que extraerlo.
        precio_base = precio_unit_pos / factor
        descuento_base = descuento_pos / factor
    else:
        # El precio ya es base imponible (o el producto es exento).
        precio_base = precio_unit_pos
        descuento_base = descuento_pos

    monto_linea_base = (precio_base * cantidad) - descuento_base

    if itbis_pct > 0:
        itbis_linea = monto_linea_base * (itbis_pct / Decimal('100'))
    else:
        itbis_linea = Decimal('0')

    return LineaCalculada(
        numero_linea=numero_linea,
        indicador_facturacion=_indicador_facturacion(itbis_pct),
        nombre_item=producto.nombre[:80],  # DGII tiene límite, truncar por seguridad
        cantidad=cantidad,
        precio_unitario_base=_q(precio_base),
        descuento_monto=_q(descuento_base),
        monto_item=_q(monto_linea_base),
        itbis_linea=_q(itbis_linea),
        itbis_pct=itbis_pct,
    )


# =============================================================================
# Mapper público
# =============================================================================

def venta_a_ecf_data(
    venta: 'Venta',
    *,
    tipo_ecf: str = '32',
    motivo_nc: str | None = None,
    encf_referencia: str | None = None,
    codigo_modificacion_nc: int | None = None,
) -> dict[str, Any]:
    """
    Convierte una Venta del POS al dict neutro `ecf_data` consumido por
    EmisorECFInterface.emitir().

    Args:
        venta: instancia de Venta con sus DetalleVenta cargados.
        tipo_ecf: '31' (crédito fiscal), '32' (consumo), '34' (NC).
                  Default '32' porque es el caso del 95% de ventas POS.
        motivo_nc: requerido si tipo_ecf == '34'.
        encf_referencia: eNCF del comprobante original al que esta NC modifica.
                         Requerido si tipo_ecf == '34'.
        codigo_modificacion_nc: 1=anulación total, 2=corrección texto,
                                3=corrección montos. Requerido si tipo_ecf == '34'.

    Returns:
        Dict con la estructura:
        {
            'tipo': str,
            'emisor': {...},          # se completa después con datos del Emisor
            'comprador': {...} | None,
            'items': [{...}, ...],
            'totales': {
                'monto_gravado_18': Decimal,
                'monto_gravado_16': Decimal,
                'monto_exento': Decimal,
                'total_itbis_18': Decimal,
                'total_itbis_16': Decimal,
                'total_itbis': Decimal,
                'monto_total': Decimal,
            },
            'metadata': {
                'venta_id': int,
                'numero_venta': str,
                'fecha_emision': date,
                'motivo_nc': str | None,
                'encf_referencia': str | None,
                'codigo_modificacion_nc': int | None,
            },
        }

    Raises:
        ValueError: si los datos de la venta son insuficientes para el
                    tipo de e-CF solicitado (tipo 31 sin RNC, NC sin
                    referencia, etc.).
    """
    if tipo_ecf not in ('31', '32', '34'):
        raise ValueError(
            f'tipo_ecf={tipo_ecf} no soportado. Válidos: 31, 32, 34.'
        )

    if tipo_ecf == '34':
        if not motivo_nc:
            raise ValueError('Nota de crédito (tipo 34) requiere motivo_nc.')
        if not encf_referencia:
            raise ValueError('Nota de crédito requiere encf_referencia.')
        if codigo_modificacion_nc not in (1, 2, 3):
            raise ValueError(
                'codigo_modificacion_nc debe ser 1 (anulación), '
                '2 (texto) o 3 (montos).'
            )

    config = get_config()

    # ----------------------------------------------------- Comprador

    comprador = _build_comprador(venta, tipo_ecf)

    # ----------------------------------------------------- Líneas y totales

    detalles = list(venta.detalles.all().select_related('producto'))
    if not detalles:
        raise ValueError(f'Venta {venta.numero_venta} no tiene detalles.')

    lineas = [
        _calcular_linea(detalle, idx + 1, config)
        for idx, detalle in enumerate(detalles)
    ]

    monto_gravado_18 = sum(
        (l.monto_item for l in lineas if l.indicador_facturacion == 1),
        Decimal('0'),
    )
    monto_gravado_16 = sum(
        (l.monto_item for l in lineas if l.indicador_facturacion == 2),
        Decimal('0'),
    )
    monto_exento = sum(
        (l.monto_item for l in lineas if l.indicador_facturacion == 4),
        Decimal('0'),
    )
    total_itbis_18 = sum(
        (l.itbis_linea for l in lineas if l.indicador_facturacion == 1),
        Decimal('0'),
    )
    total_itbis_16 = sum(
        (l.itbis_linea for l in lineas if l.indicador_facturacion == 2),
        Decimal('0'),
    )
    total_itbis = total_itbis_18 + total_itbis_16
    monto_total = monto_gravado_18 + monto_gravado_16 + monto_exento + total_itbis

    # ----------------------------------------------------- Items serializables

    items = [
        {
            'numero_linea': l.numero_linea,
            'indicador_facturacion': l.indicador_facturacion,
            'nombre_item': l.nombre_item,
            'cantidad': l.cantidad,
            'precio_unitario': l.precio_unitario_base,
            'descuento_monto': l.descuento_monto,
            'monto_item': l.monto_item,
            'itbis_pct': l.itbis_pct,
        }
        for l in lineas
    ]

    return {
        'tipo': tipo_ecf,
        'emisor': None,  # lo inyecta el orquestador desde Emisor
        'comprador': comprador,
        'items': items,
        'totales': {
            'monto_gravado_18': _q(monto_gravado_18),
            'monto_gravado_16': _q(monto_gravado_16),
            'monto_exento': _q(monto_exento),
            'total_itbis_18': _q(total_itbis_18),
            'total_itbis_16': _q(total_itbis_16),
            'total_itbis': _q(total_itbis),
            'monto_total': _q(monto_total),
        },
        'metadata': {
            'venta_id': venta.id,
            'numero_venta': venta.numero_venta,
            'fecha_emision': venta.fecha_venta.date(),
            'tipo_pago': 2 if getattr(venta, 'condicion_pago', 'CONTADO') == 'CREDITO' else 1,
            'fecha_limite_pago': _fecha_limite_pago_venta(venta),
            'motivo_nc': motivo_nc,
            'encf_referencia': encf_referencia,
            'codigo_modificacion_nc': codigo_modificacion_nc,
        },
    }


# =============================================================================
# Comprador
# =============================================================================

def _build_comprador(venta: 'Venta', tipo_ecf: str) -> dict | None:
    """
    Reglas de DGII para identificación del comprador:

    - Tipo 31 (crédito fiscal): RNC obligatorio. Cliente CONTADO
      genérico se trata como ausencia de cliente (es un placeholder
      operativo, no una identidad fiscal). Si la venta no tiene
      cliente real con RNC, fallamos explícito.

    - Tipo 32 (consumo): comprador opcional. Si hay cliente real
      con datos, los enviamos. Cliente CONTADO o ausencia: omitimos
      la sección Comprador (MSeller acepta documentos sin ella para
      tipo 32).

    - Tipo 34 (nota de crédito): replica el comprador del ECF original.
    """
    cliente = venta.cliente if hasattr(venta, 'cliente') else None

    # El Cliente CONTADO genérico (Cliente.tipo == 'CONTADO') es un
    # placeholder del sistema, no una identidad fiscal. Se trata
    # como ausencia para fines de e-CF.
    if cliente is not None and cliente.tipo == 'CONTADO':
        cliente = None

    if tipo_ecf == '31':
        if cliente is None:
            raise ValueError(
                'e-CF tipo 31 requiere cliente con RNC. '
                'La venta no tiene cliente real asignado '
                '(o el asignado es CONTADO genérico).'
            )
        rnc = _normalizar_rnc(cliente.cedula_rnc)
        if not rnc:
            raise ValueError(
                f'Cliente "{cliente.nombre}" no tiene cédula/RNC válido. '
                f'No se puede emitir tipo 31.'
            )
        return {
            'rnc_o_cedula': rnc,
            'razon_social': cliente.nombre,
            'direccion': (cliente.direccion or '').strip(),
        }

    # Tipo 32 o 34
    if cliente is None:
        return None

    rnc = _normalizar_rnc(cliente.cedula_rnc)
    return {
        'rnc_o_cedula': rnc or None,
        'razon_social': cliente.nombre,
        'direccion': (cliente.direccion or '').strip(),
    }


def _normalizar_rnc(valor: str | None) -> str:
    """
    Normaliza un RNC/cédula al formato que espera DGII: solo dígitos,
    sin guiones, espacios u otros caracteres. La validación de
    longitud (9 u 11 dígitos) la hace MSeller del lado del proveedor.

    Ejemplos:
        '131-12345-6'   -> '131123456'
        ' 40211111111 ' -> '40211111111'
        None            -> ''
        ''              -> ''
    """
    if not valor:
        return ''
    return ''.join(c for c in valor if c.isdigit())


def _fecha_limite_pago_venta(venta: 'Venta'):
    cuenta = getattr(venta, 'cuenta_por_cobrar', None)
    if cuenta is None:
        return None
    return cuenta.fecha_limite
