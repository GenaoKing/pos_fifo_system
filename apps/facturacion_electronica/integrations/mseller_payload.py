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

    Tipo 31: requiere FechaVencimientoSecuencia, FechaLimitePago si
             TipoPago=2, TotalPaginas. La validez de la secuencia la
             gestiona MSeller, pero hay que enviar el campo.
    Tipo 32: NO lleva FechaVencimientoSecuencia ni FechaLimitePago.
    Tipo 34: requiere IndicadorNotaCredito y la sección
             InformacionReferencia (en otro lugar del payload).
    """
    tipo = ecf_data['tipo']
    metadata = ecf_data['metadata']

    id_doc: dict[str, Any] = {
        'TipoeCF': tipo,
        'eNCF': encf,
        'IndicadorEnvioDiferido': 0,
        'TipoIngresos': '01',
        'TipoPago': 1,  # default contado; el POS no maneja crédito hoy
    }

    if tipo == '31':
        # FechaVencimientoSecuencia: la secuencia DGII vence al cierre
        # del año. Usamos 31-12 del año en curso. MSeller también la
        # valida del lado del proveedor.
        anio = metadata['fecha_emision'].year
        id_doc['FechaVencimientoSecuencia'] = f'31-12-{anio}'
        id_doc['IndicadorMontoGravado'] = 0
        id_doc['TotalPaginas'] = 1

    elif tipo == '32':
        id_doc['IndicadorMontoGravado'] = 0

    elif tipo == '34':
        id_doc['IndicadorNotaCredito'] = '0'
        id_doc['IndicadorMontoGravado'] = 0
        # Tipo 34 no requiere FechaVencimientoSecuencia ni TotalPaginas
        # según la doc; la sección InformacionReferencia va aparte.

    return id_doc


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

    out = {
        'RazonSocialComprador': comprador['razon_social'],
    }
    rnc = comprador.get('rnc_o_cedula')
    if rnc:
        out['RNCComprador'] = rnc
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

    totales: dict[str, Any] = {}

    if monto_gravado_total > 0:
        totales['MontoGravadoTotal'] = _num(monto_gravado_total)

    if t['monto_gravado_18'] > 0:
        totales['MontoGravadoI1'] = _num(t['monto_gravado_18'])
        totales['ITBIS1'] = 18

    if t['monto_gravado_16'] > 0:
        totales['MontoGravadoI2'] = _num(t['monto_gravado_16'])
        totales['ITBIS2'] = 16

    if t['monto_exento'] > 0:
        totales['MontoExento'] = _num(t['monto_exento'])

    if t['total_itbis'] > 0:
        totales['TotalITBIS'] = _num(t['total_itbis'])
        if t['total_itbis_18'] > 0:
            totales['TotalITBIS1'] = _num(t['total_itbis_18'])
        if t['total_itbis_16'] > 0:
            totales['TotalITBIS2'] = _num(t['total_itbis_16'])

    totales['MontoTotal'] = _num(t['monto_total'])

    return totales


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
    encabezado = {
        'Version': '1.0',
        'IdDoc': _build_id_doc(ecf_data, encf),
        'Emisor': _build_emisor(ecf_data),
        'Totales': _build_totales(ecf_data),
    }

    comprador = _build_comprador(ecf_data)
    if comprador is not None:
        encabezado['Comprador'] = comprador

    ecf_root: dict[str, Any] = {
        'Encabezado': encabezado,
        'DetallesItems': _build_items(ecf_data),
    }

    if ecf_data['tipo'] == '34':
        ecf_root['InformacionReferencia'] = _build_informacion_referencia(ecf_data)

    return {'ECF': ecf_root}