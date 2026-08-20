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


class ItemCarritoInvalidoError(ErrorVentaBase):
    """
    Una linea del carrito no tiene forma valida: falta el id, la cantidad
    no es un entero positivo, el precio no es positivo o el descuento cae
    fuera de [0, subtotal de la linea].
    """
    status_code = 400


class PrecioNoAutorizadoError(ErrorVentaBase):
    """
    El precio enviado por el cliente no coincide con ninguna fuente
    autorizada (precio vigente del producto o precio de la cotizacion
    que origina la venta).
    """
    status_code = 400


class MetodoPagoInvalidoError(ErrorVentaBase):
    """
    El metodo de pago no esta en el allowlist del sistema o esta
    deshabilitado en la configuracion del negocio.
    """
    status_code = 400


class PagosInconsistentesError(ErrorVentaBase):
    """
    Postcondicion de caja: los pagos registrados no suman el total de la
    venta. Señala un bug de armado de pagos, no un error del cajero.
    """
    status_code = 400


class CotizacionInvalidaError(ErrorVentaBase):
    """La cotizacion referida no existe, esta vencida o ya fue convertida."""
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

class VentaNoEncontradaError(ErrorVentaBase):
    """
    No existe una Venta con el id solicitado.

    Existe para que el service no dependa de `Http404` (que el view trata
    como excepción no anticipada y reporta como 500): un id inexistente es
    un resultado de negocio esperable, no una falla interna.
    """
    status_code = 404


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


class SucursalNoResueltaError(ErrorVentaBase):
    """
    La instalacion declara `SUCURSAL_CODIGO` pero no existe esa Sucursal en BD.

    Es un error de configuracion, no del cajero: seguir adelante crearia una
    venta sin sucursal, con numeracion legacy que puede chocar con la de otra
    sucursal al replicarse al cloud.
    """
    status_code = 500


class ModuloInactivoError(ErrorVentaBase):
    """La operación requiere un módulo que no está incluido en el plan del negocio
    (ej: venta a crédito con el módulo de cuentas por cobrar desactivado)."""
    status_code = 403
