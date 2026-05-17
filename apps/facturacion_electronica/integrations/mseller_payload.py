"""
apps/facturacion_electronica/integrations/mseller_payload.py

Mapper específico de MSeller: dict neutro `ecf_data` → JSON conforme al
schema que MSeller espera (basado en el formato DGII pero con la
estructura JSON de la API REST de MSeller).

Referencia: https://docs.ecf.mseller.app/docs/integration/formato-documentos-ecf

Este archivo es el ÚNICO lugar del sistema que conoce la estructura
de campos específica de MSeller (mayúsculas, nombres, tipos numéricos
vs strings). Cuando llegue la implementación nativa en Fase 2, este
archivo no se usa — se reemplaza por un builder XML directo.

Convenciones DGII observadas en la doc:
- Fechas en formato 'DD-MM-YYYY' (no ISO).
- Montos como números (float/int), no strings, salvo casos puntuales
  como TotalISRRetencion que la doc muestra como string.
- IndicadorFacturacion: 1=18%, 2=16%, 4=exento.
- IndicadorEnvioDiferido: 0 (envío inmediato), 1 (diferido).
- TipoIngresos '01' = ingresos por operaciones (caso default POS retail).
- TipoPago: 1 = contado, 2 = crédito.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any


# =============================================================================
# Helpers de formato
# =============================================================================

def _fmt_fecha(d: date) -> str:
    """DGII usa DD-MM-YYYY en todos los campos de fecha."""
    return d.strftime('%d-%m-%Y')


def _num(value: Decimal | int | float) -> float:
    """
    Convierte Decimal a float para serialización JSON.
    MSeller acepta ambos pero su doc usa números literales en los
    ejemplos (ej: 540.0, no "540.00"), así que normalizamos a float.
    """
    return float(value)


# =============================================================================
# Constructores por sección
# =============================================================================

def _build_id_doc(ecf_data: dict, encf: str) -> dict:
    """
    Sección IdDoc del encabezado. Varía según tipo de e-CF.

    Tipo 31: requiere FechaVencimientoSecuencia. En pruebas recientes
             DGII devolvio rechazo explicito cuando
             `IndicadorEnvioDiferido != 1`, asi que mantenemos 1 como
             default salvo override explicito desde configuracion.
    Tipo 32: NO lleva FechaVencimientoSecuencia ni FechaLimitePago.
    Tipo 34: requiere IndicadorNotaCredito y la sección
             InformacionReferencia (en otro lugar del payload).
    """
    tipo = ecf_data['tipo']
    metadata = ecf_data['metadata']
    emisor = ecf_data.get('emisor') or {}
    indicador_envio_diferido = emisor.get('indicador_envio_diferido')
    tipo_ingresos = emisor.get('tipo_ingresos') or '01'
    tipo_pago = emisor.get('tipo_pago')
    if tipo_pago is None:
        tipo_pago = 1
    fecha_limite_pago = emisor.get('fecha_limite_pago')

    if tipo == '31':
        if indicador_envio_diferido is None:
            indicador_envio_diferido = 1
        # La fecha de vencimiento de secuencia no debe inferirse a ciegas
        # en todos los entornos. Si el Emisor trae una fecha explícita en
        # config_proveedor (inyectada al sub-dict `emisor`), la usamos.
        # Si no existe, mantenemos el fallback histórico de 31-12 del año
        # de emisión, útil para ciertos escenarios de sandbox.
        fecha_vencimiento = (
            emisor.get('fecha_vencimiento_secuencia')
            or f'31-12-{metadata["fecha_emision"].year}'
        )
        # Para tipo 31, MSeller/DGII son sensibles al orden de los
        # campos dentro de IdDoc. Armamos el dict completo en el orden
        # de la variante mas simple observada en la documentacion y en
        # el troubleshooting actual.
        #
        # Importante: nuestros MontoItem representan base gravable sin
        # ITBIS incluido. Por eso IndicadorMontoGravado debe enviarse
        # explicitamente en 0.
        id_doc = {
            'TipoeCF': tipo,
            'eNCF': encf,
            'FechaVencimientoSecuencia': fecha_vencimiento,
            'IndicadorEnvioDiferido': indicador_envio_diferido,
            'IndicadorMontoGravado': 0,
            'TipoIngresos': tipo_ingresos,
            'TipoPago': tipo_pago,
        }
        if tipo_pago == 2 and fecha_limite_pago:
            id_doc['FechaLimitePago'] = fecha_limite_pago
        return id_doc

    if tipo == '32':
        if indicador_envio_diferido is None:
            indicador_envio_diferido = 0
        return {
            'TipoeCF': tipo,
            'eNCF': encf,
            'IndicadorEnvioDiferido': indicador_envio_diferido,
            'IndicadorMontoGravado': 0,
            'TipoIngresos': tipo_ingresos,
            'TipoPago': tipo_pago,
        }

    if tipo == '34':
        if indicador_envio_diferido is None:
            indicador_envio_diferido = 0
        return {
            'TipoeCF': tipo,
            'eNCF': encf,
            'IndicadorEnvioDiferido': indicador_envio_diferido,
            'IndicadorNotaCredito': '0',
            'IndicadorMontoGravado': 0,
            'TipoIngresos': tipo_ingresos,
            'TipoPago': tipo_pago,
        }

    raise ValueError(f'Tipo e-CF no soportado para IdDoc: {tipo}')


def _build_emisor(ecf_data: dict) -> dict:
    """
    Sección Emisor. Datos vienen del modelo Emisor inyectados por el
    orquestador antes de llamar a este mapper.
    """
    emisor = ecf_data['emisor']
    if emisor is None:
        raise ValueError(
            'ecf_data["emisor"] es None. El orquestador debe inyectar '
            'los datos del Emisor antes de construir el payload.'
        )
    return {
        'RNCEmisor': emisor['rnc'],
        'RazonSocialEmisor': emisor['razon_social'],
        'DireccionEmisor': emisor.get('direccion') or 'N/D',
        'FechaEmision': _fmt_fecha(ecf_data['metadata']['fecha_emision']),
    }


def _build_comprador(ecf_data: dict) -> dict | None:
    """
    Sección Comprador.

    - Tipo 31: obligatorio con RNC válido.
    - Tipo 32: opcional. Si no hay, MSeller acepta omitir la sección o
      usar comprador genérico ("00000000000" / "Consumidor Final").
      Política elegida: si no hay datos, omitimos la sección
      completa — más limpio que pasar valores ficticios.
    - Tipo 34: replica el del ECF original (que viene en ecf_data).
    """
    comprador = ecf_data.get('comprador')
    if comprador is None:
        return None

    # Igual que en Encabezado e IdDoc, MSeller/DGII están siendo
    # sensibles al orden de serialización. Para Comprador colocamos
    # primero RNCComprador y luego RazonSocialComprador, siguiendo
    # el orden de los ejemplos oficiales y el mensaje de rechazo
    # observado en tipo 31.
    out: dict[str, Any] = {}
    rnc = comprador.get('rnc_o_cedula')
    if rnc:
        out['RNCComprador'] = rnc
    out['RazonSocialComprador'] = comprador['razon_social']
    direccion = comprador.get('direccion')
    if direccion:
        out['DireccionComprador'] = direccion

    return out


def _build_totales(ecf_data: dict) -> dict:
    """
    Sección Totales. Estructura de la doc DGII:

    - MontoGravadoTotal: suma de bases imponibles (18% + 16%)
    - MontoGravadoI1: base imponible al 18%
    - MontoGravadoI2: base imponible al 16% (solo si hay)
    - MontoExento: suma de líneas exentas
    - ITBIS1 / ITBIS2: porcentajes (18, 16) — solo si la tasa aplica
    - TotalITBIS: suma total de ITBIS
    - TotalITBIS1 / TotalITBIS2: ITBIS desglosado por tasa
    - MontoTotal: gran total (gravado + exento + ITBIS)

    Importante: si no hay líneas al 16%, NO se envían MontoGravadoI2,
    ITBIS2, ni TotalITBIS2 — DGII rechaza si están con valor 0 cuando
    no aplican (comportamiento observado en mensajes de error MSeller).
    """
    t = ecf_data['totales']

    monto_gravado_total = t['monto_gravado_18'] + t['monto_gravado_16']

    # Igual que en Encabezado/IdDoc/Comprador, MSeller-DGII está siendo
    # sensible al orden. Para Totales seguimos el orden del ejemplo
    # oficial: gravados, exento, ITBIS porcentuales, ITBIS totales,
    # monto total y no facturable.
    totales: dict[str, Any] = {}

    if monto_gravado_total > 0:
        totales['MontoGravadoTotal'] = _num(monto_gravado_total)

    if t['monto_gravado_18'] > 0:
        totales['MontoGravadoI1'] = _num(t['monto_gravado_18'])

    if t['monto_gravado_16'] > 0:
        totales['MontoGravadoI2'] = _num(t['monto_gravado_16'])

    if t['monto_exento'] > 0:
        totales['MontoExento'] = _num(t['monto_exento'])

    if t['monto_gravado_18'] > 0:
        totales['ITBIS1'] = 18

    if t['monto_gravado_16'] > 0:
        totales['ITBIS2'] = 16

    if t['total_itbis'] > 0:
        totales['TotalITBIS'] = _num(t['total_itbis'])
        if t['total_itbis_18'] > 0:
            totales['TotalITBIS1'] = _num(t['total_itbis_18'])
        if t['total_itbis_16'] > 0:
            totales['TotalITBIS2'] = _num(t['total_itbis_16'])

    totales['MontoTotal'] = _num(t['monto_total'])

    return totales


def _build_paginacion(ecf_data: dict) -> dict:
    """
    Construye la sección Paginacion.

    Por ahora emitimos una sola página porque el POS actual no parte
    renglones de e-CF en varias páginas. Esto alinea mejor el tipo 31
    con el ejemplo de MSeller, que incluye TotalPaginas=1 y su bloque
    de paginación correspondiente.
    """
    t = ecf_data['totales']
    items = ecf_data['items']
    ultima_linea = items[-1]['numero_linea'] if items else 1

    pagina = {
        'PaginaNo': 1,
        'NoLineaDesde': 1,
        'NoLineaHasta': ultima_linea,
        'SubtotalMontoGravadoPagina': _num(
            t['monto_gravado_18'] + t['monto_gravado_16']
        ),
        'SubtotalMontoGravado1Pagina': _num(t['monto_gravado_18']),
        'SubtotalExentoPagina': _num(t['monto_exento']),
        'SubtotalItbisPagina': _num(t['total_itbis']),
        'SubtotalItbis1Pagina': _num(t['total_itbis_18']),
        'MontoSubtotalPagina': _num(t['monto_total']),
        'SubtotalMontoNoFacturablePagina': 0,
    }

    if t['monto_gravado_16'] > 0:
        pagina['SubtotalMontoGravado2Pagina'] = _num(t['monto_gravado_16'])
    if t['total_itbis_16'] > 0:
        pagina['SubtotalItbis2Pagina'] = _num(t['total_itbis_16'])

    return {'Pagina': [pagina]}


def _build_items(ecf_data: dict) -> dict:
    """
    Sección DetallesItems con array Item. Estructura por línea:
        NumeroLinea, IndicadorFacturacion, NombreItem,
        IndicadorBienoServicio (1=bien, 2=servicio),
        CantidadItem, PrecioUnitarioItem, MontoItem,
        DescuentoMonto (opcional), TablaSubDescuento (opcional)
    """
    items_out = []
    for item in ecf_data['items']:
        item_obj: dict[str, Any] = {
            'NumeroLinea': item['numero_linea'],
            'IndicadorFacturacion': item['indicador_facturacion'],
            'NombreItem': item['nombre_item'],
            'IndicadorBienoServicio': 1,  # POS de retail vende bienes
            'CantidadItem': _num(item['cantidad']),
            'PrecioUnitarioItem': _num(item['precio_unitario']),
            'MontoItem': _num(item['monto_item']),
        }
        if item['descuento_monto'] > 0:
            item_obj['DescuentoMonto'] = _num(item['descuento_monto'])
        items_out.append(item_obj)

    return {'Item': items_out}


def _build_informacion_referencia(ecf_data: dict) -> dict:
    """
    Sección InformacionReferencia (solo tipo 34).

    NCFModificado: el eNCF del ECF original que esta NC modifica.
    RNCOtroContribuyente: el RNC del comprador del ECF original.
    FechaNCFModificado: fecha de emisión del ECF original.
    CodigoModificacion: 1=anulación total, 2=corrección texto,
                        3=corrección montos.
    RazonModificacion: motivo libre (lo que el cajero escribió al anular).
    """
    metadata = ecf_data['metadata']
    comprador = ecf_data.get('comprador') or {}

    ref: dict[str, Any] = {
        'NCFModificado': metadata['encf_referencia'],
        'CodigoModificacion': metadata['codigo_modificacion_nc'],
        'RazonModificacion': metadata['motivo_nc'],
        # FechaNCFModificado idealmente debería ser la fecha del ECF
        # original. Como aproximación usamos la fecha de emisión actual
        # — MSeller no es estricto con esto, pero conviene mejorarlo
        # cuando tengamos persistido el ECF original.
        # TODO: leer fecha del ECF original desde el modelo ECF.
        'FechaNCFModificado': _fmt_fecha(metadata['fecha_emision']),
    }

    rnc_otro = comprador.get('rnc_o_cedula')
    if rnc_otro:
        ref['RNCOtroContribuyente'] = rnc_otro

    return ref


# =============================================================================
# Punto de entrada público
# =============================================================================

def build_mseller_payload(ecf_data: dict, encf: str) -> dict:
    """
    Construye el JSON completo que se envía a POST /documentos-ecf.

    Args:
        ecf_data: dict neutro producido por venta_to_ecf.venta_a_ecf_data().
                  El campo 'emisor' debe estar inyectado (no None).
        encf: número de comprobante electrónico asignado para esta emisión.
              Formato E + tipo + 10 dígitos. Lo asigna el orquestador
              consultando el rango disponible.

    Returns:
        Dict con la estructura {"ECF": {"Encabezado": {...},
        "DetallesItems": {...}, "InformacionReferencia": {...}}}
        listo para enviarse como body JSON al endpoint.
    """
    # El orden de las claves del encabezado importa para la transformación
    # JSON -> XML de MSeller. En pruebas con tipo 31 observamos que
    # serializar `Comprador` después de `Totales` termina generando un XML
    # inválido para DGII. Mantenemos el mismo orden que muestran los ejemplos
    # oficiales de MSeller: Version, IdDoc, Emisor, Comprador, Totales.
    encabezado = {
        'Version': '1.0',
        'IdDoc': _build_id_doc(ecf_data, encf),
        'Emisor': _build_emisor(ecf_data),
    }

    comprador = _build_comprador(ecf_data)
    if comprador is not None:
        encabezado['Comprador'] = comprador

    encabezado['Totales'] = _build_totales(ecf_data)

    ecf_root: dict[str, Any] = {
        'Encabezado': encabezado,
        'DetallesItems': _build_items(ecf_data),
    }

    if ecf_data['tipo'] == '31':
        # Estrategia actual para tipo 31: payload mínimo viable.
        # No enviamos Paginacion, TotalPaginas, MontoNoFacturable ni
        # FechaHoraFirma para reducir superficie de rechazo mientras
        # alineamos el contrato exacto con MSeller/DGII.
        pass

    if ecf_data['tipo'] == '34':
        ecf_root['InformacionReferencia'] = _build_informacion_referencia(ecf_data)

    return {'ECF': ecf_root}
