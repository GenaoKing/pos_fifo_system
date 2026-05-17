"""
apps/facturacion_electronica/services/procesador.py

Procesador de ECFs en cola. Función pura que toma un ECF en estado
no-terminal y lo avanza al siguiente estado contra MSeller.

Diseño:
- Sin estado entre invocaciones: cada llamada es independiente.
- Reusable desde el management command y desde tests.
- Toda la lógica de transición de estado vive aquí en un solo lugar.
- El HTTP client (MSellerHTTPClient) ya tiene reintentos a nivel HTTP;
  este procesador añade reintentos a nivel "ECF" controlados por
  el campo `ECF.intentos`.

Estados que procesa:
    PENDIENTE       → llama emitir(), pasa a ENVIADO/EN_PROCESO/RECHAZADO/ERROR
    ENVIADO         → llama consultar_estado(), pasa a APROBADO/RECHAZADO/etc
    EN_PROCESO      → llama consultar_estado(), pasa a APROBADO/RECHAZADO/etc
    ERROR           → re-llama emitir() (transitorio recuperable)
    APROBADO        → terminal, no procesa
    APROBADO_COND.  → terminal, no procesa
    RECHAZADO       → terminal, no procesa

Side effects:
- Persiste cambios en ECF (estado, encf, track_id, codigo_seguridad,
  intentos, xml_firmado, xml_respuesta).
- Crea EventoECF por cada transición.
- Todo en una sola transacción atómica por ECF.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from ..interfaces import EstadosECF, ResultadoEmision
from ..models import ECF, EventoECF
from ..integrations.mseller_payload import build_mseller_payload
from .cola_emision import (
    ColaEmisionError,
    debe_abortar_ecf_pendiente,
    recuperar_payload_nc,
)
from .factory import ECFNoConfigurado, get_emisor_ecf
from .venta_to_ecf import venta_a_ecf_data

if TYPE_CHECKING:
    from ..interfaces import EmisorECFInterface

logger = logging.getLogger('ecf.procesador')


# =============================================================================
# Resultado de procesamiento (para reporting del management command)
# =============================================================================

class ResultadoProcesamiento:
    """
    Resultado de procesar un ECF. Útil para que el management command
    arme un resumen al final del tick.
    """
    def __init__(self, ecf_id: int, estado_anterior: str, estado_nuevo: str,
                 mensaje: str, exitoso: bool):
        self.ecf_id = ecf_id
        self.estado_anterior = estado_anterior
        self.estado_nuevo = estado_nuevo
        self.mensaje = mensaje
        self.exitoso = exitoso

    def __repr__(self) -> str:
        marca = '✓' if self.exitoso else '✗'
        return (
            f'{marca} ECF#{self.ecf_id} '
            f'{self.estado_anterior} → {self.estado_nuevo}: {self.mensaje}'
        )


# =============================================================================
# Punto de entrada principal
# =============================================================================

def procesar_ecf(ecf: ECF) -> ResultadoProcesamiento:
    """
    Procesa un ECF avanzándolo al siguiente estado.

    Args:
        ecf: instancia ECF, refrescada de BD justo antes de llamar.

    Returns:
        ResultadoProcesamiento con metadata de la transición.

    No levanta excepciones: todo error se traduce a transición a
    estado ERROR con mensaje. El management command no necesita
    try/except por cada ECF.
    """
    estado_anterior = ecf.estado

    # Estados terminales: nada que hacer.
    if ecf.es_terminal():
        return ResultadoProcesamiento(
            ecf_id=ecf.id,
            estado_anterior=estado_anterior,
            estado_nuevo=estado_anterior,
            mensaje='Estado terminal, no se procesa.',
            exitoso=True,
        )

    # Tope de intentos: si ya falló 5 veces, no insistir.
    if not ecf.es_reintentable():
        _registrar_evento(
            ecf=ecf,
            estado_anterior=estado_anterior,
            estado_nuevo=ecf.estado,
            mensaje=(
                f'ECF agotó intentos ({ecf.intentos}). '
                f'Requiere intervención manual del SYSADMIN.'
            ),
            payload={'tipo_evento': 'limite_intentos'},
        )
        return ResultadoProcesamiento(
            ecf_id=ecf.id,
            estado_anterior=estado_anterior,
            estado_nuevo=ecf.estado,
            mensaje='Agotó intentos; intervención manual requerida.',
            exitoso=False,
        )

    # Aborto: la venta fue anulada antes de que el ECF se emitiera.
    debe_abortar, motivo = debe_abortar_ecf_pendiente(ecf)
    if debe_abortar:
        return _abortar_ecf(ecf, motivo, estado_anterior)

    # Despachar según estado actual
    if ecf.estado in (EstadosECF.PENDIENTE, EstadosECF.ERROR):
        return _emitir(ecf, estado_anterior)

    if ecf.estado in (EstadosECF.ENVIADO, EstadosECF.EN_PROCESO):
        return _consultar(ecf, estado_anterior)

    # Estado no contemplado (no debería pasar con los choices actuales)
    logger.error(
        f'ECF#{ecf.id} en estado no contemplado por el procesador: '
        f'{ecf.estado}'
    )
    return ResultadoProcesamiento(
        ecf_id=ecf.id,
        estado_anterior=estado_anterior,
        estado_nuevo=ecf.estado,
        mensaje=f'Estado no contemplado: {ecf.estado}',
        exitoso=False,
    )


# =============================================================================
# Sub-procesos por tipo de transición
# =============================================================================

def _emitir(ecf: ECF, estado_anterior: str) -> ResultadoProcesamiento:
    """
    ECF en PENDIENTE/ERROR → emite contra MSeller. La emisión asigna
    eNCF (si todavía no tenía), envía el payload, y persiste el
    track_id + estado retornado.
    """
    try:
        impl = get_emisor_ecf(emisor=ecf.emisor)
    except ECFNoConfigurado as exc:
        return _marcar_error(
            ecf=ecf,
            estado_anterior=estado_anterior,
            mensaje=f'Configuración faltante: {exc}',
            es_recuperable=False,
        )

    # Construir ecf_data según tipo
    try:
        ecf_data = _armar_ecf_data(ecf)
    except (ValueError, ColaEmisionError) as exc:
        # Errores de armado son no-recuperables (datos inconsistentes).
        # Marcamos RECHAZADO directo para que SYSADMIN investigue.
        with transaction.atomic():
            ecf.estado = EstadosECF.RECHAZADO
            ecf.intentos += 1
            ecf.save(update_fields=['estado', 'intentos', 'actualizado_en'])
            _registrar_evento(
                ecf=ecf,
                estado_anterior=estado_anterior,
                estado_nuevo=EstadosECF.RECHAZADO,
                mensaje=f'Error armando payload: {exc}',
                payload={'tipo_evento': 'error_armado', 'detalle': str(exc)},
            )
        return ResultadoProcesamiento(
            ecf_id=ecf.id,
            estado_anterior=estado_anterior,
            estado_nuevo=EstadosECF.RECHAZADO,
            mensaje=f'Error armando payload: {exc}',
            exitoso=False,
        )

    # Llamar al proveedor. La implementación de MSellerEmisor ya
    # captura excepciones del HTTP client y retorna ResultadoEmision
    # con exitoso=False en caso de fallo.
    resultado: ResultadoEmision = impl.emitir(ecf_data)

    return _aplicar_resultado_emision(ecf, resultado, estado_anterior, ecf_data)


def _consultar(ecf: ECF, estado_anterior: str) -> ResultadoProcesamiento:
    """
    ECF en ENVIADO/EN_PROCESO → consulta estado en MSeller. Avanza
    a APROBADO/RECHAZADO si DGII ya respondió, o se queda en
    EN_PROCESO si todavía está validando.
    """
    if not ecf.encf:
        # Sin encf no podemos consultar. Esto sería un bug del
        # procesador: ENVIADO sin encf no debería ocurrir.
        return _marcar_error(
            ecf=ecf,
            estado_anterior=estado_anterior,
            mensaje='ECF en ENVIADO sin encf asignado. Bug del procesador.',
            es_recuperable=False,
        )

    try:
        impl = get_emisor_ecf(emisor=ecf.emisor)
    except ECFNoConfigurado as exc:
        return _marcar_error(
            ecf=ecf,
            estado_anterior=estado_anterior,
            mensaje=f'Configuración faltante: {exc}',
            es_recuperable=False,
        )

    # MSeller consulta por eNCF; pasamos ecf.encf como track_id.
    estado_remoto = impl.consultar_estado(ecf.encf)

    with transaction.atomic():
        ecf.estado = estado_remoto.estado
        if estado_remoto.codigo_seguridad and not ecf.codigo_seguridad:
            ecf.codigo_seguridad = estado_remoto.codigo_seguridad
        if estado_remoto.encf and not ecf.encf:
            ecf.encf = estado_remoto.encf
        ecf.intentos += 1
        ecf.xml_respuesta = json.dumps(
            estado_remoto.raw_response, default=str, indent=2
        )
        ecf.save()

        _registrar_evento(
            ecf=ecf,
            estado_anterior=estado_anterior,
            estado_nuevo=estado_remoto.estado,
            mensaje=f'Consulta MSeller: {estado_remoto.mensaje}',
            payload={
                'tipo_evento': 'consulta_estado',
                'encf': ecf.encf,
                'raw_response': estado_remoto.raw_response,
            },
        )

    return ResultadoProcesamiento(
        ecf_id=ecf.id,
        estado_anterior=estado_anterior,
        estado_nuevo=estado_remoto.estado,
        mensaje=f'Consulta OK: {estado_remoto.mensaje}',
        exitoso=estado_remoto.estado in (
            EstadosECF.APROBADO,
            EstadosECF.APROBADO_CONDICIONAL,
            EstadosECF.EN_PROCESO,  # sigue en proceso, no es error
        ),
    )


# =============================================================================
# Helpers
# =============================================================================

def _armar_ecf_data(ecf: ECF) -> dict:
    """
    Construye el dict neutro `ecf_data` para que el proveedor lo emita.

    Para tipos 31/32: usa venta_a_ecf_data() directo desde la venta.
    Para tipo 34: recupera del EventoECF inicial el motivo, encf
    referenciado y código de modificación.
    """
    if ecf.venta is None:
        raise ColaEmisionError(
            f'ECF#{ecf.id} sin venta asociada. No se puede armar payload.'
        )

    if ecf.tipo in ('31', '32'):
        return venta_a_ecf_data(venta=ecf.venta, tipo_ecf=ecf.tipo)

    if ecf.tipo == '34':
        nc_data = recuperar_payload_nc(ecf)
        return venta_a_ecf_data(
            venta=ecf.venta,
            tipo_ecf='34',
            motivo_nc=nc_data['motivo'],
            encf_referencia=nc_data['encf_referencia'],
            codigo_modificacion_nc=nc_data['codigo_modificacion'],
        )

    raise ColaEmisionError(f'Tipo de ECF no soportado: {ecf.tipo}')


def _aplicar_resultado_emision(
    ecf: ECF,
    resultado: ResultadoEmision,
    estado_anterior: str,
    ecf_data: dict,
) -> ResultadoProcesamiento:
    """
    Aplica un ResultadoEmision al ECF: persiste cambios y registra
    evento. Centralizado para evitar drift entre _emitir() y otros
    posibles puntos de aplicación a futuro.
    """
    with transaction.atomic():
        ecf.estado = resultado.estado_inicial
        # En re-emisiones del mismo ECF (ej: secuencia previa rechazada),
        # la nueva respuesta puede traer un eNCF distinto. Debemos
        # sobrescribirlo para que el documento siga apuntando al intento
        # vigente y futuras consultas/polling usen la secuencia correcta.
        if resultado.encf:
            ecf.encf = resultado.encf
        if resultado.track_id:
            ecf.track_id = resultado.track_id

        # Código de seguridad y QR vienen en raw_response de MSeller
        # incluso antes de que DGII apruebe (MSeller los genera al
        # firmar localmente). Los persistimos para que el ticket
        # térmico los pueda imprimir.
        raw = resultado.raw_response or {}
        if raw.get('securityCode'):
            ecf.codigo_seguridad = raw['securityCode']

        # Persistir el JSON enviado como evidencia local (Semana 2
        # decisión: en MSeller no podemos descargar el XML firmado,
        # guardamos el payload JSON. En reintentos guardamos la última
        # versión enviada, que es la relevante para troubleshooting.
        ecf.xml_firmado = json.dumps(ecf_data, default=str, indent=2)

        ecf.xml_respuesta = json.dumps(raw, default=str, indent=2)
        ecf.intentos += 1
        ecf.save()

        _registrar_evento(
            ecf=ecf,
            estado_anterior=estado_anterior,
            estado_nuevo=resultado.estado_inicial,
            mensaje=resultado.mensaje,
            payload={
                'tipo_evento': 'emision',
                'exitoso': resultado.exitoso,
                'encf': resultado.encf,
                'track_id': resultado.track_id,
                'raw_response': raw,
            },
        )

    return ResultadoProcesamiento(
        ecf_id=ecf.id,
        estado_anterior=estado_anterior,
        estado_nuevo=resultado.estado_inicial,
        mensaje=resultado.mensaje,
        exitoso=resultado.exitoso,
    )


def _abortar_ecf(
    ecf: ECF,
    motivo: str,
    estado_anterior: str,
) -> ResultadoProcesamiento:
    """
    Aborta un ECF antes de enviarlo a MSeller. Usado cuando la venta
    fue anulada antes de que el ECF se procesara. Se marca como
    RECHAZADO con motivo claro; no consume secuencia DGII.
    """
    with transaction.atomic():
        ecf.estado = EstadosECF.RECHAZADO
        ecf.save(update_fields=['estado', 'actualizado_en'])
        _registrar_evento(
            ecf=ecf,
            estado_anterior=estado_anterior,
            estado_nuevo=EstadosECF.RECHAZADO,
            mensaje=f'Aborto pre-emisión: {motivo}',
            payload={'tipo_evento': 'aborto_pre_emision', 'motivo': motivo},
        )

    return ResultadoProcesamiento(
        ecf_id=ecf.id,
        estado_anterior=estado_anterior,
        estado_nuevo=EstadosECF.RECHAZADO,
        mensaje=f'Abortado: {motivo}',
        exitoso=True,  # aborto controlado, no es fallo del sistema
    )


def _marcar_error(
    *,
    ecf: ECF,
    estado_anterior: str,
    mensaje: str,
    es_recuperable: bool,
) -> ResultadoProcesamiento:
    """
    Marca un ECF como ERROR (recuperable, vuelve a la cola) o
    RECHAZADO (no recuperable, requiere intervención).
    """
    estado_nuevo = EstadosECF.ERROR if es_recuperable else EstadosECF.RECHAZADO
    with transaction.atomic():
        ecf.estado = estado_nuevo
        ecf.intentos += 1
        ecf.save(update_fields=['estado', 'intentos', 'actualizado_en'])
        _registrar_evento(
            ecf=ecf,
            estado_anterior=estado_anterior,
            estado_nuevo=estado_nuevo,
            mensaje=mensaje,
            payload={
                'tipo_evento': 'error_procesamiento',
                'recuperable': es_recuperable,
            },
        )

    return ResultadoProcesamiento(
        ecf_id=ecf.id,
        estado_anterior=estado_anterior,
        estado_nuevo=estado_nuevo,
        mensaje=mensaje,
        exitoso=False,
    )


def _registrar_evento(
    *,
    ecf: ECF,
    estado_anterior: str,
    estado_nuevo: str,
    mensaje: str,
    payload: dict,
) -> EventoECF:
    """
    Crea un EventoECF. Centralizado para garantizar consistencia
    en el formato de payload (siempre dict con `tipo_evento`).
    """
    return EventoECF.objects.create(
        ecf=ecf,
        estado_anterior=estado_anterior,
        estado_nuevo=estado_nuevo,
        mensaje=mensaje[:5000],  # límite defensivo, mensajes muy largos en logs
        payload=payload,
    )
