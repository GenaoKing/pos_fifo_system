"""
apps/suscripciones/registry.py
Registro declarativo de modulos + su grafo de dependencias (fuente de verdad).

Un "modulo" es una unidad funcional vendible (o core). Aqui se declara su `key`,
sus dependencias (`depende_de`), si es `core` (siempre activo, no vendible) y el
flag legacy de `ConfiguracionNegocio` del que se deriva su estado actual para la
migracion sin disrupcion.

Agregar/editar un modulo = editar esta lista. La tabla `Modulo` (DB) es un espejo
sembrado desde aqui (`manage.py sync_modulos`); el grafo de dependencias vive solo
en codigo.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Modulo:
    key: str
    nombre: str
    descripcion: str = ''
    depende_de: tuple = ()
    core: bool = False
    # Campo de ConfiguracionNegocio (ej. 'modulo_ecf') para derivar el estado
    # actual en la migracion. None = no tenia flag (se decide aparte).
    flag_legacy: str = None


CATALOGO_MODULOS = [
    # --- Nucleo (core: siempre activo, no vendible) -------------------------
    Modulo('productos', 'Catalogo de productos', 'Productos y categorias.', core=True),
    Modulo('inventario', 'Inventario / FIFO', 'Lotes, compras y costeo FIFO.',
           depende_de=('productos',), core=True),
    Modulo('clientes', 'Clientes', 'Maestro de clientes.', core=True),
    Modulo('ventas', 'Ventas (POS)', 'Punto de venta.',
           depende_de=('productos', 'inventario'), core=True),
    Modulo('caja', 'Caja / turnos', 'Apertura/cierre de caja y turnos.',
           depende_de=('ventas',), core=True),

    # --- Vendibles (satelites; dependen del nucleo) ------------------------
    Modulo('cuentas_por_cobrar', 'Cuentas por cobrar (credito)',
           'Ventas a credito, cartera y abonos.',
           depende_de=('ventas', 'clientes')),  # historicamente sin flag (siempre on)
    Modulo('ecf', 'Facturacion electronica (e-CF)',
           'Emision de comprobantes fiscales electronicos.',
           depende_de=('ventas',), flag_legacy='modulo_ecf'),
    Modulo('financiacion', 'Financiacion cooperativa',
           'Registro y PDF de financiacion cooperativa.',
           depende_de=('ventas',), flag_legacy='modulo_financiacion_coop'),
    Modulo('cotizaciones', 'Cotizaciones',
           'Crear cotizaciones y convertirlas en ventas.',
           depende_de=('ventas', 'productos'), flag_legacy='modulo_cotizaciones'),
    Modulo('etiquetas_zebra', 'Etiquetas Zebra',
           'Impresion de etiquetas con impresora Zebra.',
           depende_de=('productos',), flag_legacy='modulo_etiquetas_zebra'),
    Modulo('reportes_ondemand', 'Reportes on-demand',
           'Generacion de reportes personalizados.',
           depende_de=('ventas',), flag_legacy='modulo_reportes_ondemand'),
    Modulo('dashboard', 'Dashboard de metricas',
           'Panel de control con metricas.',
           depende_de=('ventas',), flag_legacy='modulo_dashboard'),
    Modulo('impresion_termica', 'Impresion termica',
           'Tickets en impresora termica.', flag_legacy='modulo_impresion_termica'),
    Modulo('barcode_scanner', 'Lector de codigo de barras',
           'Lectura de codigos de barras en el POS.',
           flag_legacy='modulo_barcode_scanner'),
]

_BY_KEY = {m.key: m for m in CATALOGO_MODULOS}


def modulo(key):
    return _BY_KEY.get(key)


def keys():
    return [m.key for m in CATALOGO_MODULOS]


def core_keys():
    return {m.key for m in CATALOGO_MODULOS if m.core}


def vendibles():
    return [m for m in CATALOGO_MODULOS if not m.core]


def cierre_dependencias(keys_iter):
    """Las keys dadas + todas sus dependencias transitivas. Ignora keys desconocidas."""
    resultado = set()
    pendientes = list(keys_iter)
    while pendientes:
        k = pendientes.pop()
        if k in resultado:
            continue
        m = _BY_KEY.get(k)
        if m is None:
            continue
        resultado.add(k)
        pendientes.extend(m.depende_de)
    return resultado


def dependientes_de(key):
    """Keys que dependen (directa o transitivamente) de `key`."""
    return {
        m.key for m in CATALOGO_MODULOS
        if m.key != key and key in cierre_dependencias((m.key,))
    }


def validar(keys_iter):
    """Devuelve el set de keys desconocidas (no presentes en el registro)."""
    return {k for k in keys_iter if k not in _BY_KEY}
