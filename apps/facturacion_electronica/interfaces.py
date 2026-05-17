"""
apps/facturacion_electronica/interfaces.py

Contrato abstracto para la emisión de e-CF, agnóstico al proveedor.
Cualquier implementación concreta (MSeller, librería nativa futura)
solo cumple esta interfaz; el resto del sistema no se entera de qué
proveedor está activo.

Ningún módulo fuera de `services/` debe importar implementaciones
concretas. Solo se conoce esta interfaz.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import ECF


# =============================================================================
# Vocabulario de estados — compartido entre interfaz y modelo
# =============================================================================

class EstadosECF:
    """
    Enumeración de los estados posibles de un e-CF a lo largo de su
    ciclo de vida. Definido como clase de constantes (no Enum) para
    integración nativa con Django choices y comparación directa con
    strings persistidos.
    """
    PENDIENTE = 'PENDIENTE'                            # creado en BD, aún no enviado al proveedor
    ENVIADO = 'ENVIADO'                                # entregado al proveedor, sin respuesta DGII
    EN_PROCESO = 'EN_PROCESO'                          # DGII recibió, validando
    APROBADO = 'APROBADO'                              # DGII aprobó, eNCF + código seguridad disponibles
    APROBADO_CONDICIONAL = 'APROBADO_CONDICIONAL'      # aprobado con observaciones DGII
    RECHAZADO = 'RECHAZADO'                            # DGII rechazó, no es válido fiscalmente
    ERROR = 'ERROR'                                    # falla técnica (red, timeout, proveedor caído)

    CHOICES = (
        (PENDIENTE, 'Pendiente'),
        (ENVIADO, 'Enviado'),
        (EN_PROCESO, 'En proceso'),
        (APROBADO, 'Aprobado'),
        (APROBADO_CONDICIONAL, 'Aprobado con observaciones'),
        (RECHAZADO, 'Rechazado'),
        (ERROR, 'Error'),
    )

    # Estados que ameritan reintento por el management command
    REINTENTABLES = frozenset({PENDIENTE, ENVIADO, EN_PROCESO, ERROR})

    # Estados terminales — no transicionan más
    TERMINALES = frozenset({APROBADO, APROBADO_CONDICIONAL, RECHAZADO})


# =============================================================================
# DTOs — neutros, sin acoplamiento a ningún proveedor
# =============================================================================

@dataclass
class ResultadoEmision:
    """
    Resultado de invocar `emitir()` o `emitir_nota_credito()`.

    `track_id` puede ser None si la emisión nunca alcanzó al proveedor
    (error de red previo). `exitoso` indica si la entrega al proveedor
    fue OK; el estado DGII final puede llegar después vía polling.
    """
    exitoso: bool
    estado_inicial: str                              # uno de EstadosECF.*
    track_id: str | None = None
    encf: str | None = None                          # si el proveedor lo asigna sincrónico
    mensaje: str = ''
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass
class EstadoECF:
    """
    Resultado de `consultar_estado()`. Snapshot del estado del documento
    en el momento de la consulta al proveedor.
    """
    track_id: str
    estado: str                                      # uno de EstadosECF.*
    encf: str | None = None
    codigo_seguridad: str | None = None
    mensaje: str = ''
    raw_response: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Interfaz abstracta
# =============================================================================

class EmisorECFInterface(ABC):
    """
    Contrato que toda implementación de proveedor de e-CF debe cumplir.

    Implementaciones concretas viven en `services/`:
      - services/mseller_emisor.py    (Semana 2)
      - services/nativo_emisor.py     (Fase 2)

    El código de ventas obtiene la implementación correcta vía un factory
    que lee `ConfiguracionNegocio.ecf_proveedor`. Nunca instancia
    directamente una clase concreta.
    """

    @abstractmethod
    def emitir(self, ecf_data: dict[str, Any]) -> ResultadoEmision:
        """
        Emite un e-CF. Sincrónico respecto al proveedor pero asíncrono
        respecto a DGII: la respuesta puede ser ENVIADO/EN_PROCESO sin
        que DGII haya aprobado todavía.

        Forma esperada de `ecf_data` (a formalizar al integrar MSeller
        en Semana 2; documentado acá como referencia inicial):
            {
                'tipo': '31' | '32' | '34',
                'emisor': {'rnc': str, 'razon_social': str, ...},
                'comprador': {...} | None,           # None para tipo 32 sin RNC
                'items': [{'codigo', 'descripcion', 'cantidad',
                           'precio', 'itbis_pct', ...}, ...],
                'totales': {'subtotal', 'descuento', 'itbis', 'total'},
                'referencia_ecf': str | None,        # encf original (solo tipo 34)
            }
        """
        raise NotImplementedError

    @abstractmethod
    def consultar_estado(self, track_id: str) -> EstadoECF:
        """
        Polling de estado contra el proveedor. Usado por el management
        command de reintentos para ECFs en estados ENVIADO o EN_PROCESO.
        """
        raise NotImplementedError

    @abstractmethod
    def emitir_nota_credito(
        self,
        ecf_original: 'ECF',
        motivo: str,
    ) -> ResultadoEmision:
        """
        Emite una NC tipo 34 que referencia un ECF previamente APROBADO.
        Se invoca al anular una venta cuyo ECF original está aprobado.
        """
        raise NotImplementedError

    @abstractmethod
    def descargar_xml_aprobado(self, track_id: str) -> bytes:
        """
        Recupera el XML firmado y aprobado por DGII desde el proveedor,
        para almacenamiento local. Es nuestra red de seguridad si en el
        futuro cambiamos de PSFE.
        """
        raise NotImplementedError