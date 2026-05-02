"""
apps/facturacion_electronica/services/mseller_emisor.py

Orquestador que implementa EmisorECFInterface usando MSeller como
proveedor concreto. Es el punto de entrada del proveedor: combina
HTTP client + payload mapper y traduce las respuestas a los DTOs
neutros que define la interfaz.

Responsabilidades:
- Implementar los 4 métodos de EmisorECFInterface.
- Asignar el eNCF antes de enviar (consultando el rango disponible
  para el emisor — por ahora delegado a TODO; MSeller acepta cualquier
  secuencia válida en TesteCF).
- Traducir respuestas MSeller (status: "Aceptado", "Rechazado", etc.)
  al vocabulario neutro EstadosECF.
- Capturar excepciones del HTTP client y convertirlas en ResultadoEmision
  con exitoso=False — la capa superior decide si reintentar.

NO responsabilidades:
- Persistir ECF/EventoECF (lo hace el caller en services/ventas o el
  hook async).
- Decidir tipo de e-CF (lo decide el cajero/UI, llega como parámetro).
- Manejar la transacción Django (el caller usa transaction.on_commit).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..interfaces import (
    EmisorECFInterface,
    EstadoECF,
    EstadosECF,
    ResultadoEmision,
)
from ..integrations.mseller_payload import build_mseller_payload
from .mseller_http_client import (
    MSellerAuthError,
    MSellerConfig,
    MSellerError,
    MSellerHTTPClient,
    MSellerValidationError,
)
from .venta_to_ecf import venta_a_ecf_data

if TYPE_CHECKING:
    from ..models import ECF, Emisor
    from apps.ventas.models import Venta

logger = logging.getLogger('ecf.mseller')


# =============================================================================
# Mapeo de estados MSeller → EstadosECF
# =============================================================================
# La doc MSeller usa: "Aceptado", "Rechazado", "Aceptado Condicional",
# "RECIBIDO", "PROCESANDO", "ERROR". También "Aceptado" minúsculas/
# title case mezcladas. Normalizamos antes de mapear.

_STATUS_MAP: dict[str, str] = {
    'aceptado': EstadosECF.APROBADO,
    'aceptado condicional': EstadosECF.APROBADO_CONDICIONAL,
    'rechazado': EstadosECF.RECHAZADO,
    'recibido': EstadosECF.ENVIADO,
    'procesando': EstadosECF.EN_PROCESO,
    'en proceso': EstadosECF.EN_PROCESO,
    'error': EstadosECF.ERROR,
}


def _map_mseller_status(status: str | None) -> str:
    if not status:
        return EstadosECF.ENVIADO
    return _STATUS_MAP.get(status.strip().lower(), EstadosECF.EN_PROCESO)


# =============================================================================
# Asignación de eNCF
# =============================================================================

def _siguiente_encf(emisor: 'Emisor', tipo: str) -> str:
    """
    Asigna el próximo eNCF disponible para el emisor y tipo dado.

    Estrategia Fase Inicial (TesteCF):
      MSeller acepta cualquier secuencia válida en ambiente de pruebas.
      La doc indica que se debe "buscar una secuencia aleatoria que no
      haya sido utilizada" durante testing. Usamos un contador local
      basado en el último eNCF emitido por este emisor para este tipo.

    Estrategia producción (eCF):
      DGII asigna rangos de secuencias al RNC. MSeller administra el
      rango activo. Hay que consultar el rango y respetarlo. Esto se
      implementa en Semana 4 cuando se haga el onboarding del piloto.

    TODO Semana 4: integrar consulta de rangos disponibles. Por ahora,
    secuencia local con padding correcto al formato E + tipo + 10 dígitos.
    """
    from ..models import ECF

    ultimo = (
        ECF.objects
        .filter(emisor=emisor, tipo=tipo)
        .exclude(encf='')
        .order_by('-encf')
        .first()
    )
    if ultimo and ultimo.encf:
        try:
            secuencia = int(ultimo.encf[3:]) + 1
        except (ValueError, IndexError):
            secuencia = 1
    else:
        secuencia = 1

    return f'E{tipo}{secuencia:010d}'


# =============================================================================
# Implementación de EmisorECFInterface
# =============================================================================

class MSellerEmisor(EmisorECFInterface):
    """
    Una instancia por emisor. Mantiene el HTTP client (con su token
    cacheado) durante el proceso. Si se cambia la config del emisor
    en el admin, hay que reinstanciar — pero como cada llamada lo
    construye nuevo desde el factory, no es problema.
    """

    def __init__(self, emisor: 'Emisor'):
        self.emisor = emisor
        config = MSellerConfig.from_emisor_config(emisor.config_proveedor)
        self.http = MSellerHTTPClient(config)

    # ------------------------------------------------------------- helpers

    def _emisor_dict(self) -> dict[str, Any]:
        """Serializa el Emisor al sub-dict que espera el mapper."""
        return {
            'rnc': self.emisor.rnc,
            'razon_social': self.emisor.razon_social,
            'nombre_comercial': self.emisor.nombre_comercial or '',
            'direccion': self.emisor.direccion or '',
        }

    def _ecf_data_para_venta(
        self,
        venta: 'Venta',
        tipo_ecf: str,
        **kwargs,
    ) -> dict:
        """Construye el dict neutro y le inyecta los datos del Emisor."""
        ecf_data = venta_a_ecf_data(venta, tipo_ecf=tipo_ecf, **kwargs)
        ecf_data['emisor'] = self._emisor_dict()
        return ecf_data

    # --------------------------------------------------------- emitir

    def emitir(self, ecf_data: dict[str, Any]) -> ResultadoEmision:
        """
        Envía el documento a MSeller. Sincrónico al proveedor pero
        asíncrono respecto a DGII: una respuesta exitosa significa
        "MSeller recibió y firmó", no "DGII aprobó".

        Forma esperada de ecf_data: la del mapper venta_a_ecf_data,
        con el campo 'emisor' ya inyectado.
        """
        if ecf_data.get('emisor') is None:
            ecf_data['emisor'] = self._emisor_dict()

        tipo = ecf_data['tipo']
        encf = _siguiente_encf(self.emisor, tipo)
        payload = build_mseller_payload(ecf_data, encf)

        logger.info(
            f'MSeller emitir: venta={ecf_data["metadata"]["numero_venta"]} '
            f'tipo={tipo} encf={encf}'
        )

        try:
            response = self.http.enviar_documento(payload)
        except MSellerValidationError as exc:
            # Error estructural en el documento. NO es transitorio:
            # reintentar lo mismo va a fallar igual. El caller debe
            # marcarlo RECHAZADO y revisar.
            logger.error(
                f'MSeller validation failed para encf={encf}: '
                f'{exc.validation_errors}'
            )
            return ResultadoEmision(
                exitoso=False,
                estado_inicial=EstadosECF.RECHAZADO,
                encf=encf,
                mensaje=str(exc),
                raw_response={'validation_errors': exc.validation_errors},
            )
        except MSellerAuthError as exc:
            logger.critical(f'MSeller auth error: {exc}')
            return ResultadoEmision(
                exitoso=False,
                estado_inicial=EstadosECF.ERROR,
                encf=encf,
                mensaje=f'Auth error: {exc}',
                raw_response=exc.response_body,
            )
        except MSellerError as exc:
            # Cualquier otro error MSeller (rate limit, server error,
            # red): tratable con reintentos del management command.
            logger.warning(f'MSeller error transitorio para encf={encf}: {exc}')
            return ResultadoEmision(
                exitoso=False,
                estado_inicial=EstadosECF.ERROR,
                encf=encf,
                mensaje=str(exc),
                raw_response=exc.response_body,
            )

        # Respuesta OK: MSeller asignó internalTrackId y firmó.
        # El estado fiscal final lo da DGII tras polling.
        return ResultadoEmision(
            exitoso=True,
            estado_inicial=EstadosECF.ENVIADO,
            track_id=response.get('internalTrackId'),
            encf=response.get('ecf') or encf,
            mensaje='Documento enviado a MSeller, pendiente DGII.',
            raw_response=response,
        )

    # ------------------------------------------------------ consultar_estado

    def consultar_estado(self, track_id: str) -> EstadoECF:
        """
        MSeller consulta por eNCF, no por trackId. El parámetro de la
        interfaz se llama track_id por neutralidad pero en esta
        implementación contiene el eNCF — el caller debe pasar el
        ECF.encf, no el ECF.track_id.

        TODO: revisar si conviene cambiar el nombre del parámetro de
        la interfaz a `identificador` en una refinación post-Semana 1.
        Por ahora se mantiene track_id para no romper la abstracción.
        """
        try:
            response = self.http.consultar_documento(track_id)
        except MSellerError as exc:
            logger.warning(f'MSeller consulta falló para {track_id}: {exc}')
            return EstadoECF(
                track_id=track_id,
                estado=EstadosECF.ERROR,
                mensaje=str(exc),
                raw_response=exc.response_body,
            )

        return EstadoECF(
            track_id=response.get('internalTrackId') or track_id,
            estado=_map_mseller_status(response.get('status')),
            encf=response.get('ncf') or response.get('ecf'),
            codigo_seguridad=response.get('securityCode'),
            mensaje=response.get('status', ''),
            raw_response=response,
        )

    # ----------------------------------------------------- emitir_nota_credito

    def emitir_nota_credito(
        self,
        ecf_original: 'ECF',
        motivo: str,
    ) -> ResultadoEmision:
        """
        Emite una NC tipo 34 referenciando un ECF previamente APROBADO.

        El código de modificación se asume 1 (anulación total) porque
        es el caso del flujo POS al anular una venta. Si en el futuro
        hace falta corrección de monto/texto, se agrega un parámetro.
        """
        venta = ecf_original.venta
        if venta is None:
            return ResultadoEmision(
                exitoso=False,
                estado_inicial=EstadosECF.ERROR,
                mensaje=(
                    f'ECF original {ecf_original.encf} no tiene venta '
                    'asociada. No se puede emitir NC automática.'
                ),
            )

        ecf_data = self._ecf_data_para_venta(
            venta,
            tipo_ecf='34',
            motivo_nc=motivo,
            encf_referencia=ecf_original.encf,
            codigo_modificacion_nc=1,
        )

        return self.emitir(ecf_data)

    # --------------------------------------------------- descargar_xml_aprobado

    def descargar_xml_aprobado(self, track_id: str) -> bytes:
        """
        MSeller no expone un endpoint público de descarga del XML
        firmado. La consulta retorna `signedXml` como ruta interna
        ("102320705/documents/TesteCF/...xml") que no es accesible
        directamente vía API.

        Estrategia Fase Inicial:
          Persistimos el JSON enviado en ECF.xml_firmado como evidencia
          local. No es el XML firmado de DGII pero sí el documento
          fiscalmente equivalente que enviamos.

        TODO consultar a soporte MSeller: ¿hay endpoint de descarga
        del XML firmado? Si no, el almacenamiento local de la doc dice
        que MSeller retiene 10 años — confirmar SLA y política si
        eventualmente migramos de PSFE.

        Por ahora: levantar NotImplementedError. El management command
        de descarga (sección 2.3 del roadmap) puede capturarlo y
        registrarlo como pendiente sin fallar.
        """
        raise NotImplementedError(
            'MSeller no expone endpoint de descarga del XML firmado. '
            'Ver TODO en MSellerEmisor.descargar_xml_aprobado.'
        )