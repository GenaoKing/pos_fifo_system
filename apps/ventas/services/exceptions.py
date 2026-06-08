"""
apps/ventas/services/exceptions.py

Excepciones tipadas que los services levantan para señalar errores
de negocio al view. El view las traduce a JsonResponse con código
HTTP apropiado.

Diseño:
- Cada excepción mapea 1:1 a un código HTTP esperado por el frontend.
- El mensaje es seguro de mostrar al usuario (no contiene detalles
  internos como stack traces o IDs de objetos sensibles).
- ErrorVentaBase es la raíz: el view captura solo esta y deriva el
  status code del atributo `status_code` de cada subclase.
"""
from __future__ import annotations


class ErrorVentaBase(Exception):
    """
    Raíz de errores de negocio de ventas/anulaciones.

    Subclases definen `status_code` que el view usa al armar la
    JsonResponse. Mensaje (str(exc)) va al campo 'error' del JSON.
    """
    status_code: int = 400

    def __init__(self, mensaje: str, *, contexto: dict | None = None):
        super().__init__(mensaje)
        self.contexto = contexto or {}


# =============================================================================
# Errores de procesar_venta
# =============================================================================

class CarritoVacioError(ErrorVentaBase):
    """El carrito enviado por el POS no tiene items."""
    status_code = 400


class TotalInconsistenteError(ErrorVentaBase):
    """
    El total enviado por el frontend no coincide con la suma calculada
    desde el carrito. Indica desincronización entre cliente y servidor.
    """
    status_code = 400


class PagoMixtoInconsistenteError(ErrorVentaBase):
    """
    En pago mixto, la suma de los montos por método no coincide con
    el total. Crítico para e-CF: DGII rechaza si no cuadra al céntimo.
    """
    status_code = 400


class StockInsuficienteError(ErrorVentaBase):
    """
    Algún producto del carrito no tiene stock suficiente y la
    configuración no permite inventario negativo.
    """
    status_code = 400


class ProductoInexistenteError(ErrorVentaBase):
    """Algún producto del carrito ya no existe en BD."""
    status_code = 404


class TipoECFInvalidoError(ErrorVentaBase):
    """
    El tipo de e-CF solicitado por el cajero no es soportado o no
    aplica para los datos de la venta (ej: tipo 31 sin cliente con RNC).
    """
    status_code = 400


class ClienteCreditoInvalidoError(ErrorVentaBase):
    """La venta a credito no tiene un cliente real activo apto para CxC."""
    status_code = 400


class MetodoPlazoCreditoInvalidoError(ErrorVentaBase):
    """El metodo de plazo solicitado no existe, esta inactivo o no aplica."""
    status_code = 400


class LimiteCreditoExcedidoError(ErrorVentaBase):
    """El saldo pendiente mas la nueva venta excede el limite del cliente."""
    status_code = 403


# =============================================================================
# Errores de anular_venta
# =============================================================================

class AnulacionNoPermitidaError(ErrorVentaBase):
    """La venta no puede anularse: ya está anulada, fuera de plazo, etc."""
    status_code = 400


class MotivoAnulacionInvalidoError(ErrorVentaBase):
    """Motivo vacío, muy corto, o ausente."""
    status_code = 400


class FIFORollbackError(ErrorVentaBase):
    """
    La devolución de stock vía FIFO falló. La transacción se hace
    rollback automáticamente y la venta queda intacta.
    """
    status_code = 500


# =============================================================================
# Errores genéricos
# =============================================================================

class PermisoDenegadoError(ErrorVentaBase):
    """El usuario no tiene rol para la operación solicitada."""
    status_code = 403
