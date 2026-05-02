"""
apps/facturacion_electronica/services/cola_emision.py

Cola de emisión de e-CF. Es el punto de entrada que el flujo de venta
usa para "encolar" emisiones sin esperar a MSeller en hot path.

Cómo funciona:
- `encolar_emision(venta, tipo_ecf)` crea un ECF en estado PENDIENTE
  asociado a la venta, registra un EventoECF inicial. NO llama a
  MSeller. Retorna inmediato.
- `encolar_nota_credito(venta, motivo)` busca el ECF aprobado de la
  venta original y crea un ECF tipo 34 en PENDIENTE referenciándolo.
  También sin llamar a MSeller.
- El management command `ecf_procesar_pendientes` (siguiente bloque)
  toma los ECFs en PENDIENTE/ERROR/EN_PROCESO con intentos < 5 y
  los procesa contra MSeller.

Ventajas del diseño:
- La cajera no espera la latencia MSeller (~2s típico, picos de 30s+).
- Si MSeller está caído, la venta se cierra normal y los ECFs se
  acumulan en PENDIENTE; al volver MSeller, el command los procesa.
- Failures en la emisión no afectan al flujo POS.
- DGII permite Envío Diferido (24h de plazo), así que cualquier
  retraso < 24h sigue cumpliendo normativa.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction

from apps.configuracion.utils import get_config

from ..interfaces import EstadosECF
from ..models import ECF, EventoECF

if TYPE_CHECKING:
    from apps.ventas.models import Venta

logger = logging.getLogger('ecf.cola')


# =============================================================================
# Excepciones
# =============================================================================

class ColaEmisionError(Exception):
    """Error genérico al encolar emisión."""


class EmisorNoConfigurado(ColaEmisionError):
    """ConfiguracionNegocio.modulo_ecf=True pero falta emisor_activo."""


class ECFOriginalNoEncontrado(ColaEmisionError):
    """Se quiso emitir NC tipo 34 pero no hay ECF aprobado de la venta."""


# =============================================================================
# Encolar emisión normal (tipos 31 / 32)
# =============================================================================

def encolar_emision(*, venta: 'Venta', tipo_ecf: str) -> ECF | None:
    """
    Crea un ECF en estado PENDIENTE para la venta dada y registra
    el evento inicial. El management command de procesamiento lo
    levanta en su próximo tick.

    Args:
        venta: instancia Venta ya commiteada (post-commit hook).
        tipo_ecf: '31' o '32'. El cajero lo eligió en el POS.

    Returns:
        Instancia ECF creada, o None si no hay emisor configurado
        (en ese caso se loguea warning y retorna sin levantar).
        El comportamiento "silencioso" es a propósito: el flujo de
        venta nunca debe romperse por config faltante de e-CF.

    Raises:
        ColaEmisionError ante errores no recuperables (ej: tipo
        de ECF inválido). El caller (hook post-commit) los captura
        y loguea sin propagar.
    """
    if tipo_ecf not in ('31', '32'):
        raise ColaEmisionError(
            f'tipo_ecf inválido para emisión normal: {tipo_ecf}. '
            f'Usar "31" o "32". Para NC usar encolar_nota_credito().'
        )

    config = get_config()
    if not config.modulo_ecf:
        logger.warning(
            f'encolar_emision llamado pero modulo_ecf=False. '
            f'venta={venta.numero_venta}. Ignorando.'
        )
        return None

    emisor = config.emisor_activo
    if emisor is None:
        # No es excepción porque la venta ya commiteó; logueamos para
        # que el SYSADMIN lo investigue.
        logger.error(
            f'encolar_emision: ConfiguracionNegocio.emisor_activo es None. '
            f'venta={venta.numero_venta} tipo={tipo_ecf}. '
            f'Configurar Emisor en admin.'
        )
        return None

    # Crear ECF + evento en una sola transacción para que sean atómicos
    # entre sí. NO depende del atomic de la venta (ya cerró).
    with transaction.atomic():
        ecf = ECF.objects.create(
            emisor=emisor,
            venta=venta,
            tipo=tipo_ecf,
            estado=EstadosECF.PENDIENTE,
            proveedor_usado=emisor.proveedor_actual,
            intentos=0,
        )
        EventoECF.objects.create(
            ecf=ecf,
            estado_anterior='',
            estado_nuevo=EstadosECF.PENDIENTE,
            mensaje=(
                f'ECF encolado tras venta {venta.numero_venta}. '
                f'Pendiente de procesar por ecf_procesar_pendientes.'
            ),
            payload={
                'venta_id': venta.id,
                'venta_numero': venta.numero_venta,
                'tipo_ecf': tipo_ecf,
                'origen': 'encolar_emision',
            },
        )

    logger.info(
        f'ECF encolado: id={ecf.id} venta={venta.numero_venta} '
        f'tipo={tipo_ecf} emisor={emisor.rnc}'
    )
    return ecf


# =============================================================================
# Encolar Nota de Crédito (tipo 34) tras anulación
# =============================================================================

def encolar_nota_credito(*, venta: 'Venta', motivo: str) -> ECF | None:
    """
    Crea un ECF tipo 34 (NC) en PENDIENTE referenciando el ECF
    APROBADO de la venta. El management command lo procesa después.

    Flujo:
        1. Buscar el ECF original de la venta en estado APROBADO o
           APROBADO_CONDICIONAL.
        2. Si no existe (ECF rechazado, en proceso, sin ECF), no se
           emite NC: la cola de emisión sabrá no procesar el ECF
           original al detectar la venta ANULADA.
        3. Si existe, crear ECF tipo 34 referenciando el original.
           El motivo se guarda en EventoECF.payload para que el
           procesador lo use al armar el JSON MSeller.

    Args:
        venta: instancia Venta ya marcada como ANULADA y commiteada.
        motivo: texto del motivo (ya validado por el service).

    Returns:
        Instancia ECF tipo 34 creada, o None si no hay ECF original
        aprobado (caso normal cuando se anula una venta sin ECF
        emitido todavía).
    """
    config = get_config()
    if not config.modulo_ecf:
        return None

    emisor = config.emisor_activo
    if emisor is None:
        logger.error(
            f'encolar_nota_credito: emisor_activo es None. '
            f'venta={venta.numero_venta}. Configurar Emisor en admin.'
        )
        return None

    # Buscar ECF original aprobado para esta venta. Filtramos por tipo
    # 31/32 para excluir NCs previas (no se anula una NC con otra NC
    # automáticamente desde acá).
    ecf_original = (
        ECF.objects
        .filter(
            venta=venta,
            tipo__in=('31', '32'),
            estado__in=(
                EstadosECF.APROBADO,
                EstadosECF.APROBADO_CONDICIONAL,
            ),
        )
        .order_by('-creado_en')
        .first()
    )

    if ecf_original is None:
        # No hay ECF aprobado para esta venta. Caso esperado cuando
        # se anula rápido y el ECF todavía no fue procesado/aprobado.
        # El procesador de cola, al ver venta.estado=='ANULADA',
        # debe abortar el procesamiento del ECF original PENDIENTE.
        logger.info(
            f'encolar_nota_credito: venta={venta.numero_venta} '
            f'no tiene ECF aprobado. NC tipo 34 NO se emite. '
            f'La cola abortará el ECF original si está PENDIENTE.'
        )
        return None

    with transaction.atomic():
        ecf_nc = ECF.objects.create(
            emisor=emisor,
            venta=venta,
            tipo='34',
            estado=EstadosECF.PENDIENTE,
            proveedor_usado=emisor.proveedor_actual,
            intentos=0,
        )
        EventoECF.objects.create(
            ecf=ecf_nc,
            estado_anterior='',
            estado_nuevo=EstadosECF.PENDIENTE,
            mensaje=(
                f'NC tipo 34 encolada. Anula ECF original {ecf_original.encf}. '
                f'Pendiente de procesar.'
            ),
            payload={
                'venta_id': venta.id,
                'venta_numero': venta.numero_venta,
                'origen': 'encolar_nota_credito',
                'motivo': motivo,
                'ecf_original_id': ecf_original.id,
                'ecf_original_encf': ecf_original.encf,
                'codigo_modificacion': 1,  # 1 = anulación total
            },
        )

    logger.info(
        f'NC tipo 34 encolada: id={ecf_nc.id} '
        f'venta={venta.numero_venta} '
        f'ecf_original={ecf_original.encf}'
    )
    return ecf_nc


# =============================================================================
# Helpers para el procesador
# =============================================================================
# Estos helpers viven acá porque son contraparte del encolado:
# el procesador necesita saber, dado un ECF en cola, qué hacer con él.

def debe_abortar_ecf_pendiente(ecf: ECF) -> tuple[bool, str]:
    """
    Determina si un ECF en estado PENDIENTE/ENVIADO/EN_PROCESO debe
    abortarse antes de enviarlo a MSeller. Casos:

    - La venta fue anulada antes de que el ECF se procesara: no
      tiene sentido emitir un ECF tipo 31/32 que después habría que
      anular con NC. Se marca RECHAZADO con motivo y se cancela.

    - El emisor activo cambió y ya no coincide con el del ECF:
      situación rara, conviene flagear.

    Returns:
        (debe_abortar: bool, motivo: str)
    """
    if ecf.tipo in ('31', '32') and ecf.venta:
        if ecf.venta.estado == 'ANULADA':
            return (
                True,
                f'Venta {ecf.venta.numero_venta} fue anulada antes de '
                f'que el ECF llegara a DGII. Se aborta emisión.'
            )

    return (False, '')


def recuperar_payload_nc(ecf: ECF) -> dict:
    """
    Para un ECF tipo 34 en cola, recupera del EventoECF inicial los
    datos necesarios para construir el ecf_data (motivo, encf
    referencia, código de modificación).

    El procesador llama a esto antes de armar el payload de MSeller.

    Returns:
        Dict con 'motivo', 'encf_referencia', 'codigo_modificacion'.
    """
    evento_inicial = (
        ecf.eventos
        .filter(estado_anterior='')
        .order_by('fecha')
        .first()
    )
    if evento_inicial is None or not evento_inicial.payload:
        raise ColaEmisionError(
            f'ECF tipo 34 id={ecf.id} no tiene evento inicial con '
            f'payload de NC. No se puede procesar.'
        )

    payload = evento_inicial.payload
    return {
        'motivo': payload.get('motivo', 'Anulación'),
        'encf_referencia': payload.get('ecf_original_encf'),
        'codigo_modificacion': payload.get('codigo_modificacion', 1),
    }