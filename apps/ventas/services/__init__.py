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
    AnulacionConAbonosError,
    AnulacionNoPermitidaError,
    CarritoVacioError,
    ClienteCreditoInvalidoError,
    CotizacionInvalidaError,
    ErrorVentaBase,
    FIFORollbackError,
    ItemCarritoInvalidoError,
    LimiteCreditoExcedidoError,
    MetodoPagoInvalidoError,
    MetodoPlazoCreditoInvalidoError,
    MotivoAnulacionInvalidoError,
    PagoMixtoInconsistenteError,
    PagosInconsistentesError,
    PermisoDenegadoError,
    PrecioNoAutorizadoError,
    ProductoInexistenteError,
    StockInsuficienteError,
    SucursalNoResueltaError,
    TipoECFInvalidoError,
    TotalInconsistenteError,
    VentaNoEncontradaError,
)
from .ventas_service import procesar_venta_service

__all__ = [
    'procesar_venta_service',
    'anular_venta_service',
    # Excepciones
    'ErrorVentaBase',
    'AnulacionConAbonosError',
    'AnulacionNoPermitidaError',
    'CarritoVacioError',
    'ClienteCreditoInvalidoError',
    'CotizacionInvalidaError',
    'FIFORollbackError',
    'ItemCarritoInvalidoError',
    'LimiteCreditoExcedidoError',
    'MetodoPagoInvalidoError',
    'MetodoPlazoCreditoInvalidoError',
    'MotivoAnulacionInvalidoError',
    'PagoMixtoInconsistenteError',
    'PagosInconsistentesError',
    'PermisoDenegadoError',
    'PrecioNoAutorizadoError',
    'ProductoInexistenteError',
    'StockInsuficienteError',
    'SucursalNoResueltaError',
    'TipoECFInvalidoError',
    'TotalInconsistenteError',
    'VentaNoEncontradaError',
]
