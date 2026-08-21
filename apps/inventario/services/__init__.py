"""
apps/inventario/services/__init__.py

API publica de los services de inventario.

    from apps.inventario.services import registrar_ajuste_service, ErrorInventarioBase
"""
from .ajustes_service import registrar_ajuste_service
from .exceptions import (
    AjusteInvalidoError,
    ErrorInventarioBase,
    LoteNoEncontradoError,
    StockInsuficienteLoteError,
)

__all__ = [
    'registrar_ajuste_service',
    'ErrorInventarioBase',
    'AjusteInvalidoError',
    'LoteNoEncontradoError',
    'StockInsuficienteLoteError',
]
