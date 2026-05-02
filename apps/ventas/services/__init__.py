"""
apps/ventas/services/__init__.py

API pública del paquete services de ventas. Importar desde acá:

    from apps.ventas.services import (
        procesar_venta_service,
        anular_venta_service,
        ErrorVentaBase,
    )
"""
from .anulaciones_service import anular_venta_service
from .exceptions import (
    AnulacionNoPermitidaError,
    CarritoVacioError,
    ErrorVentaBase,
    FIFORollbackError,
    MotivoAnulacionInvalidoError,
    PagoMixtoInconsistenteError,
    PermisoDenegadoError,
    ProductoInexistenteError,
    StockInsuficienteError,
    TipoECFInvalidoError,
    TotalInconsistenteError,
)
from .ventas_service import procesar_venta_service

__all__ = [
    'procesar_venta_service',
    'anular_venta_service',
    # Excepciones
    'ErrorVentaBase',
    'AnulacionNoPermitidaError',
    'CarritoVacioError',
    'FIFORollbackError',
    'MotivoAnulacionInvalidoError',
    'PagoMixtoInconsistenteError',
    'PermisoDenegadoError',
    'ProductoInexistenteError',
    'StockInsuficienteError',
    'TipoECFInvalidoError',
    'TotalInconsistenteError',
]