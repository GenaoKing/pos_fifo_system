"""
apps/facturacion_electronica/services/factory.py

Punto único de obtención de implementaciones de EmisorECFInterface.

El código de negocio (apps/ventas, hooks de anulación, management
commands) llama a `get_emisor_ecf()` y recibe la implementación
correcta según la configuración activa de la sucursal. Nadie más
instancia clases concretas de proveedor.

Esto es lo que hace el swap MSeller → nativa de Fase 2 ser un cambio
de configuración (`ecf_proveedor='nativo'`) y no un rewrite.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from apps.configuracion.utils import get_config

from ..interfaces import EmisorECFInterface

if TYPE_CHECKING:
    from ..models import Emisor


class ECFNoConfigurado(Exception):
    """ConfiguracionNegocio.modulo_ecf=True pero falta config completa."""


class ProveedorECFNoSoportado(Exception):
    """ecf_proveedor apunta a un valor sin implementación registrada."""


def get_emisor_ecf(emisor: 'Emisor | None' = None) -> EmisorECFInterface:
    """
    Retorna una implementación lista para usar de EmisorECFInterface.

    Args:
        emisor: si se provee, usa este Emisor (útil para management
                commands que iteran varios). Si es None, lee
                ConfiguracionNegocio.emisor_activo de la sucursal actual.

    Raises:
        ECFNoConfigurado: si modulo_ecf=False, o emisor_activo está
                          sin definir, o el Emisor no tiene
                          config_proveedor poblado.
        ProveedorECFNoSoportado: si ecf_proveedor apunta a 'nativo'
                                 (Fase 2, no implementado todavía).
    """
    config = get_config()

    if not config.modulo_ecf:
        raise ECFNoConfigurado(
            'modulo_ecf=False en la configuracion. '
            'Activarlo en admin antes de emitir.'
        )

    if emisor is None:
        emisor = config.emisor_activo
        if emisor is None:
            raise ECFNoConfigurado(
                'ConfiguracionNegocio no tiene emisor_activo asignado. '
                'Crear un Emisor en admin y asociarlo a la sucursal.'
            )

    if not emisor.config_proveedor:
        raise ECFNoConfigurado(
            f'Emisor {emisor.rnc} no tiene config_proveedor poblado. '
            'Definir email_env, password_env, api_key_env, entorno.'
        )

    proveedor = emisor.proveedor_actual

    if proveedor == 'mseller':
        # Import local para no cargar el HTTP client si se usa nativo
        from .mseller_emisor import MSellerEmisor
        return MSellerEmisor(emisor)

    if proveedor == 'nativo':
        raise ProveedorECFNoSoportado(
            'El proveedor "nativo" (dgii-ecf-py) no está implementado '
            'todavía. Es trabajo de Fase 2. Cambiar a "mseller" en '
            'admin del Emisor.'
        )

    raise ProveedorECFNoSoportado(
        f'Proveedor "{proveedor}" no reconocido. '
        f'Valores válidos: mseller, nativo.'
    )