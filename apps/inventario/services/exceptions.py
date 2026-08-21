"""
apps/inventario/services/exceptions.py

Excepciones tipadas de los services de inventario. Mismo patron que
`apps/ventas/services/exceptions.py`: cada una mapea 1:1 a un status HTTP y su
mensaje es seguro de mostrar al usuario.
"""
from __future__ import annotations


class ErrorInventarioBase(Exception):
    """Raiz de errores de negocio de inventario."""
    status_code: int = 400

    def __init__(self, mensaje: str, *, contexto: dict | None = None):
        super().__init__(mensaje)
        self.contexto = contexto or {}


class AjusteInvalidoError(ErrorInventarioBase):
    """Tipo, cantidad o motivo del ajuste fuera de contrato."""
    status_code = 400


class StockInsuficienteLoteError(ErrorInventarioBase):
    """La salida solicitada supera las existencias del lote."""
    status_code = 400


class LoteNoEncontradoError(ErrorInventarioBase):
    """
    No existe un lote activo con ese id.

    Tipada para que el view responda 404 y no 500: un id inexistente es un
    resultado esperable, no una falla interna.
    """
    status_code = 404
