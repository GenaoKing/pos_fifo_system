"""
apps/ventas/services/ventas_service.py

Servicio de procesamiento de venta. Encapsula toda la lógica de
negocio que antes vivía dentro del view `procesar_venta` de
apps/ventas/views.py.

Responsabilidades:
- Validar la forma del payload (carrito, importes, método de pago, total)
- Autorizar la operación contra RBAC (`ventas.crear`, `ventas.aplicar_descuento`)
- Resolver los datos que NO se le creen al cliente: sucursal, precio, cotización
- Validar stock disponible según ConfiguracionNegocio
- Crear Venta + DetalleVenta + Pago en una sola transacción atómica
- Consumir stock vía FIFO y persistir el costo consumido en cada detalle
- Registrar auditoría DENTRO del atomic
- Disparar hooks DESPUÉS del commit:
    * sync engine (ya existente)
    * impresión térmica (ya extraída en Semana 0)
    * encolado de e-CF si modulo_ecf=True (NUEVO en Semana 3)

NO responsabilidades (las maneja el view):
- Parseo de JSON del request
- Construcción de JsonResponse
- Manejo de status HTTP

Frontera transaccional
----------------------
Todo lo que define QUÉ es la venta (identidad de sucursal, precios, stock,
cotización de origen, pagos, tipo fiscal) se decide y se persiste dentro del
mismo `transaction.atomic()`. Lo único diferido a `on_commit` son efectos
externos que no deben poder tumbar una venta ya cobrada: impresión, snapshot
de inventario y encolado del e-CF.

Patrón de uso desde el view:
    try:
        venta = procesar_venta_service(
            usuario=request.user,
            datos=json.loads(request.body),
        )
        return JsonResponse({'success': True, 'venta': {...}})
    except ErrorVentaBase as exc:
        return JsonResponse(
            {'success': False, 'error': str(exc)},
            status=exc.status_code,
        )
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import models, transaction

from apps.auditoria.models import Auditoria
from apps.configuracion.utils import get_config, modulo_activo
from apps.inventario.fifo_logic import procesar_venta_fifo
from apps.inventario.models import Lote
from apps.productos.models import Producto
from apps.sync import events as sync_events
from utils.impresoras.manager import print_manager

from ..models import DetalleVenta, Pago, Venta
from .exceptions import (
    CarritoVacioError,
    ClienteCreditoInvalidoError,
    CotizacionInvalidaError,
    ItemCarritoInvalidoError,
    MetodoPagoInvalidoError,
    MetodoPlazoCreditoInvalidoError,
    ModuloInactivoError,
    PagoMixtoInconsistenteError,
    PagosInconsistentesError,
    PermisoDenegadoError,
    PrecioNoAutorizadoError,
    ProductoInexistenteError,
    StockInsuficienteError,
    SucursalNoResueltaError,
    TipoECFInvalidoError,
    TotalInconsistenteError,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

logger = logging.getLogger('ventas.service')


# =============================================================================
# Tipos de e-CF aceptados desde el POS
# =============================================================================
# El cajero elige uno de estos antes de cerrar venta. Default '32' (consumo).
# El tipo 34 (NC) NO se elige desde el POS: lo dispara la anulación.
TIPOS_ECF_POS = ('31', '32')
TIPO_ECF_DEFAULT = '32'

# =============================================================================
# Métodos de pago
# =============================================================================
# Allowlist del servidor. `_registrar_pagos` sólo sabe construir Pago para
# estos valores; cualquier otro string producía una venta SIN ningún Pago
# (venta que suma a ingresos y no aparece en el cierre de caja).
METODOS_PAGO_VALIDOS = ('efectivo', 'transferencia', 'tarjeta', 'mixto', 'credito')

# Métodos simples -> flag de ConfiguracionNegocio que los habilita.
# 'mixto' se valida por sus componentes; 'credito' por el módulo de CxC.
FLAG_CONFIG_POR_METODO = {
    'efectivo': 'pago_efectivo',
    'transferencia': 'pago_transferencia',
    'tarjeta': 'pago_tarjeta',
}

# Tolerancia de redondeo al comparar importes en pesos.
CENTAVO = Decimal('0.01')

# Techo defensivo por línea. No es una regla de negocio: evita que un payload
# manipulado desborde `DecimalField(max_digits=12)` y reviente con un 500 en
# vez de un 400 legible.
CANTIDAD_MAXIMA_LINEA = 1_000_000


# =============================================================================
# Punto de entrada principal
# =============================================================================

def procesar_venta_service(
    *,
    usuario: 'AbstractUser',
    datos: dict[str, Any],
    ip_address: str | None = None,
) -> Venta:
    """
    Procesa una venta completa desde datos parseados del POS.

    Args:
        usuario: usuario autenticado que está cerrando la venta. Debe tener
            el permiso `ventas.crear`; si el carrito trae descuentos, además
            `ventas.aplicar_descuento`.
        datos: dict ya parseado del JSON del request. Forma esperada:
            {
                'carrito': [
                    {'id': int, 'cantidad': int, 'precio_venta': str|float,
                     'descuento': str|float (opcional)},
                    ...
                ],
                'metodo_pago': 'efectivo' | 'transferencia' | 'tarjeta' | 'mixto' | 'credito',
                'monto_efectivo': str|float (opcional),
                'monto_transferencia': str|float (opcional),
                'monto_tarjeta': str|float (opcional),
                'referencia_tarjeta': str (opcional),
                'total': str|float,
                'cliente_id': int (opcional),
                'cotizacion_id': int (opcional),
                'tipo_ecf': '31' | '32' (opcional, default '32'),
            }
        ip_address: IP del cliente, para auditoría. None si no se pudo extraer.

    Returns:
        Instancia Venta persistida y commiteada.

    Raises:
        ErrorVentaBase y subclases ante fallos de validación o negocio.
        Cualquier otra excepción propaga (el view la captura como 500).
    """
    # ----------------------- Parseo y validación de tipos primitivos
    carrito = datos.get('carrito') or []
    metodo_pago = (datos.get('metodo_pago') or 'efectivo').strip().lower()
    es_credito = metodo_pago == 'credito'
    credito_data = datos.get('credito') or {}
    monto_efectivo = _decimal(datos.get('monto_efectivo', 0))
    monto_transferencia = _decimal(datos.get('monto_transferencia', 0))
    monto_tarjeta = _decimal(datos.get('monto_tarjeta', 0))
    referencia_tarjeta = (datos.get('referencia_tarjeta') or '').strip()
    total_esperado = _decimal(datos.get('total', 0))
    cliente_id = datos.get('cliente_id')
    cotizacion_id = datos.get('cotizacion_id')
    tipo_ecf = (datos.get('tipo_ecf') or TIPO_ECF_DEFAULT).strip()

    # ----------------------- Validaciones pre-transacción (lectura pura)
    if not carrito:
        raise CarritoVacioError('El carrito está vacío.')

    # Normaliza y valida rangos línea por línea. A partir de acá el resto del
    # service trabaja con enteros/Decimals ya saneados, no con lo que mandó
    # el navegador.
    items = _normalizar_carrito(carrito)

    if total_esperado <= 0:
        raise TotalInconsistenteError(
            f'El total de la venta debe ser positivo. Recibido: ${total_esperado}.'
        )

    if tipo_ecf not in TIPOS_ECF_POS:
        raise TipoECFInvalidoError(
            f'Tipo de e-CF "{tipo_ecf}" no soportado desde el POS. '
            f'Válidos: {", ".join(TIPOS_ECF_POS)}.'
        )

    config = get_config()
    sucursal = _resolver_sucursal()

    # Autorización server-side. El catálogo RBAC declara estos permisos; hasta
    # ahora sólo los aplicaba la UI, así que un POST directo los saltaba.
    _autorizar(usuario=usuario, items=items, sucursal=sucursal)

    _validar_metodo_pago(
        metodo_pago,
        config=config,
        monto_efectivo=monto_efectivo,
        monto_transferencia=monto_transferencia,
        monto_tarjeta=monto_tarjeta,
        credito_data=credito_data,
    )

    if metodo_pago == 'mixto':
        suma_pagos = monto_efectivo + monto_transferencia + monto_tarjeta
        if abs(suma_pagos - total_esperado) > CENTAVO:
            raise PagoMixtoInconsistenteError(
                f'La suma de los pagos no coincide con el total. '
                f'Total: ${total_esperado}, suma: ${suma_pagos} '
                f'(efectivo: ${monto_efectivo}, '
                f'transferencia: ${monto_transferencia}, '
                f'tarjeta: ${monto_tarjeta}).'
            )
    elif es_credito:
        monto_inicial = _decimal(credito_data.get('monto_inicial', 0))
        metodo_inicial = (credito_data.get('metodo_inicial') or 'efectivo').strip()
        if monto_inicial <= 0:
            pass
        elif metodo_inicial == 'mixto':
            suma_inicial = (
                _decimal(credito_data.get('monto_efectivo', monto_efectivo))
                + _decimal(credito_data.get('monto_transferencia', monto_transferencia))
                + _decimal(credito_data.get('monto_tarjeta', monto_tarjeta))
            )
            if abs(suma_inicial - monto_inicial) > CENTAVO:
                raise PagoMixtoInconsistenteError(
                    f'La suma del inicial no coincide. '
                    f'Inicial: ${monto_inicial}, suma: ${suma_inicial}.'
                )
        elif metodo_inicial not in ('efectivo', 'transferencia', 'tarjeta'):
            raise MetodoPlazoCreditoInvalidoError('Metodo de inicial para credito invalido.')

        if monto_inicial > total_esperado + CENTAVO:
            raise PagoMixtoInconsistenteError(
                f'El inicial (${monto_inicial}) no puede superar el total '
                f'de la venta (${total_esperado}).'
            )

    # El modulo de cuentas por cobrar debe estar activo para vender a credito.
    # Falla rapido antes de tocar inventario/transaccion. La UI del POS no debe
    # ofrecer credito si el modulo esta off; este es el guard de servidor.
    if es_credito and not modulo_activo('cuentas_por_cobrar'):
        raise ModuloInactivoError(
            'El modulo de cuentas por cobrar no esta incluido en el plan del negocio; '
            'no se puede registrar una venta a credito.'
        )

    ecf_activo = modulo_activo('ecf')

    # ----------------------- Transacción atómica
    with transaction.atomic():
        # La cotización se bloquea ANTES de tocar inventario: dos cajeros con
        # la misma cotización pendiente se serializan acá y el segundo recibe
        # un error de negocio en vez de generar una segunda venta.
        cotizacion = _resolver_cotizacion(cotizacion_id)

        productos = _cargar_productos(items)
        _validar_precios(items, productos, cotizacion)
        _validar_stock(
            items,
            productos,
            permitir_negativo=config.permitir_inventario_negativo,
        )

        cliente = _resolver_cliente(
            cliente_id,
            condicion_pago='CREDITO' if es_credito else 'CONTADO',
        )

        # Precondición fiscal ANTES del commit: el encolado del e-CF corre
        # post-commit, así que un tipo 31 sin comprador válido dejaba una venta
        # cerrada, cobrada y fiscalmente inemitible.
        if ecf_activo:
            _validar_precondiciones_ecf(tipo_ecf=tipo_ecf, cliente=cliente)

        venta = _crear_venta(
            usuario=usuario,
            items=items,
            cliente=cliente,
            sucursal=sucursal,
            total_esperado=total_esperado,
            condicion_pago='CREDITO' if es_credito else 'CONTADO',
        )

        _crear_detalles_y_consumir_fifo(
            venta=venta,
            items=items,
            productos=productos,
            usuario=usuario,
            permitir_negativo=config.permitir_inventario_negativo,
        )

        _registrar_pagos(
            venta=venta,
            metodo_pago=metodo_pago,
            monto_efectivo=monto_efectivo,
            monto_transferencia=monto_transferencia,
            monto_tarjeta=monto_tarjeta,
            referencia_tarjeta=referencia_tarjeta,
            credito_data=credito_data,
        )
        _verificar_pagos(venta)

        if cotizacion is not None:
            _marcar_cotizacion_convertida(cotizacion, venta)

        # Outbox transaccional: el evento se escribe DENTRO de la transaccion,
        # atomico con la venta. Va ANTES de crear_cuenta_para_venta a proposito:
        # el handler cloud de CXC_CREADA rechaza la cuenta si su venta todavia
        # no llego, y los eventos se empujan en orden de creacion.
        sync_events.evento_venta_creada(venta)

        if es_credito:
            from apps.cuentas_por_cobrar.services import crear_cuenta_para_venta

            crear_cuenta_para_venta(
                venta=venta,
                usuario=usuario,
                credito_data=credito_data,
                ip_address=ip_address,
            )

        # Auditoría dentro del atomic (atómica con la venta)
        Auditoria.registrar_venta(
            venta=venta,
            usuario=usuario,
            ip_address=ip_address,
        )

        # ------------------ Hooks post-commit
        # NOTA(perf): el snapshot recorre todos los productos activos (O(N)
        # consultas FIFO) y serializa el inventario completo en el payload.
        # Hacerlo por venta es caro en catalogos grandes; pendiente moverlo a
        # un snapshot periodico (comando/cron) y dejar el tiempo real a los
        # eventos de movimiento por linea.
        # Por ese costo se queda FUERA de la transaccion: es una foto de estado,
        # no un hecho de negocio, y perderla es inocuo (la siguiente la
        # reemplaza). El evento de la venta si es transaccional, mas arriba.
        transaction.on_commit(lambda: sync_events.evento_inventario_snapshot())

        # Impresión fuera de la transacción: si la térmica falla, no
        # se hace rollback de la venta. Mismo patrón que sync.
        transaction.on_commit(
            lambda v=venta, u=usuario: _hook_imprimir_ticket(v, u)
        )

        # Encolado de e-CF (Semana 3): solo si el módulo está activo.
        # Crea ECF en estado PENDIENTE; el management command
        # ecf_procesar_pendientes lo levanta y lo emite contra MSeller.
        # Esto desacopla el flujo de venta del flujo fiscal: la cajera
        # no espera por DGII. Las precondiciones deterministas del tipo
        # fiscal ya se validaron arriba, dentro de la transacción.
        if ecf_activo:
            transaction.on_commit(
                lambda v=venta, t=tipo_ecf: _hook_encolar_ecf(v, t)
            )

    return venta


# =============================================================================
# Helpers internos
# =============================================================================

def _decimal(valor: Any) -> Decimal:
    """Conversión defensiva a Decimal vía str para no perder precisión."""
    return Decimal(str(valor or 0))


def _dinero(valor: Any) -> Decimal:
    """Decimal redondeado a centavos, para comparar importes entre fuentes."""
    return _decimal(valor).quantize(CENTAVO)


# -----------------------------------------------------------------------------
# Payload
# -----------------------------------------------------------------------------

def _normalizar_carrito(carrito: Any) -> list[dict]:
    """
    Valida la forma de cada línea y devuelve items saneados:
        {'producto_id': int, 'cantidad': int, 'precio': Decimal, 'descuento': Decimal}

    Los modelos declaran validators (`MinValueValidator`), pero
    `objects.create()` no ejecuta `full_clean()`, así que sin esta pasada una
    línea con cantidad o precio negativo llegaba hasta la BD y producía una
    venta con importes imposibles.
    """
    if not isinstance(carrito, list):
        raise ItemCarritoInvalidoError('El carrito debe ser una lista de items.')

    items: list[dict] = []

    for posicion, crudo in enumerate(carrito, start=1):
        if not isinstance(crudo, dict):
            raise ItemCarritoInvalidoError(
                f'El item #{posicion} del carrito no tiene forma válida.'
            )

        producto_id = _entero_positivo(crudo.get('id'), posicion, 'id de producto')
        cantidad = _entero_positivo(crudo.get('cantidad'), posicion, 'cantidad')

        if cantidad > CANTIDAD_MAXIMA_LINEA:
            raise ItemCarritoInvalidoError(
                f'La cantidad del item #{posicion} ({cantidad}) excede el máximo '
                f'permitido por línea ({CANTIDAD_MAXIMA_LINEA}).'
            )

        try:
            precio = _dinero(crudo.get('precio_venta'))
            descuento = _dinero(crudo.get('descuento', 0))
        except (InvalidOperation, ValueError, TypeError):
            raise ItemCarritoInvalidoError(
                f'El item #{posicion} tiene importes no numéricos.'
            )

        if precio <= 0:
            raise ItemCarritoInvalidoError(
                f'El precio del item #{posicion} debe ser positivo. Recibido: ${precio}.'
            )

        subtotal = precio * cantidad
        if descuento < 0:
            raise ItemCarritoInvalidoError(
                f'El descuento del item #{posicion} no puede ser negativo.'
            )
        if descuento > subtotal:
            raise ItemCarritoInvalidoError(
                f'El descuento del item #{posicion} (${descuento}) supera el '
                f'subtotal de la línea (${subtotal}).'
            )

        items.append({
            'producto_id': producto_id,
            'cantidad': cantidad,
            'precio': precio,
            'descuento': descuento,
        })

    return items


def _entero_positivo(valor: Any, posicion: int, campo: str) -> int:
    """Convierte a int > 0 o levanta ItemCarritoInvalidoError con contexto."""
    try:
        entero = int(valor)
    except (TypeError, ValueError):
        raise ItemCarritoInvalidoError(
            f'El item #{posicion} no tiene un {campo} válido.'
        )
    if entero <= 0:
        raise ItemCarritoInvalidoError(
            f'El {campo} del item #{posicion} debe ser mayor que cero. '
            f'Recibido: {entero}.'
        )
    return entero


# -----------------------------------------------------------------------------
# Autorización
# -----------------------------------------------------------------------------

def _autorizar(*, usuario: 'AbstractUser', items: list[dict], sucursal) -> None:
    """
    Aplica el RBAC del catálogo sobre la operación de venta.

    Vive en el service (y no sólo en un decorador del view) para que cualquier
    entrada — POS, script, futuro endpoint — quede cubierta por la misma regla.
    """
    if not _tiene_permiso(usuario, 'ventas.crear', sucursal):
        raise PermisoDenegadoError(
            'No tienes permisos para registrar ventas (ventas.crear).'
        )

    if any(item['descuento'] > 0 for item in items):
        if not _tiene_permiso(usuario, 'ventas.aplicar_descuento', sucursal):
            raise PermisoDenegadoError(
                'No tienes permisos para aplicar descuentos '
                '(ventas.aplicar_descuento).'
            )


def _tiene_permiso(usuario, codigo: str, sucursal) -> bool:
    comprobar = getattr(usuario, 'tiene_permiso', None)
    if comprobar is None:
        # Usuario sin el modelo propio (AnonymousUser o doble de test).
        return False
    return bool(comprobar(codigo, sucursal=sucursal))


# -----------------------------------------------------------------------------
# Sucursal
# -----------------------------------------------------------------------------

def _resolver_sucursal():
    """
    Sucursal de esta instalación, o None si no puede resolverse.

    La venta se creaba SIEMPRE sin sucursal, así que el modelo caía a la
    numeración legacy `V-<fecha>-<n>` y el payload de sync viajaba con
    `sucursal_codigo=None`. Dos instalaciones podían emitir `V-20260820-0001`
    y el cloud, que deduplica por `numero_venta`, descartaba la segunda venta
    como reenvío de la primera — pérdida silenciosa.

    Cuándo falla explícito: sólo si esta instalación replica al cloud
    (`SYNC_ENABLED`) y aun así no puede resolver su sucursal. Ahí seguir
    adelante sí produce la colisión de identidad descrita arriba, y es
    preferible parar la caja a facturar ventas que no van a llegar.

    Una instalación standalone (sin sync) sigue operando con sucursal None:
    nada se replica, no hay identidad que colisionar, y `SUCURSAL_CODIGO`
    tiene un default de settings que no expresa intención del operador.
    """
    from apps.sucursales.models import get_sucursal_actual

    sucursal = get_sucursal_actual()
    if sucursal is not None:
        return sucursal

    codigo = getattr(settings, 'SUCURSAL_CODIGO', None)

    if getattr(settings, 'SYNC_ENABLED', False):
        raise SucursalNoResueltaError(
            f'La instalación sincroniza con el cloud como sucursal "{codigo}" '
            f'pero no existe esa sucursal en la base de datos. Sin ella la venta '
            f'se numera como legacy y el cloud puede descartarla por colisión. '
            f'Corré "manage.py verificar_instalacion" antes de facturar.'
        )

    logger.warning(
        'Venta sin sucursal: no se pudo resolver "%s" y la instalación es '
        'standalone (SYNC_ENABLED=False). Se usa numeración legacy.',
        codigo,
    )
    return None


# -----------------------------------------------------------------------------
# Productos, precios y stock
# -----------------------------------------------------------------------------

def _cargar_productos(items: list[dict]) -> dict[int, Producto]:
    """Trae de una sola vez los productos del carrito, indexados por id."""
    ids = {item['producto_id'] for item in items}
    productos = {p.id: p for p in Producto.objects.filter(id__in=ids)}

    faltantes = ids - set(productos)
    if faltantes:
        raise ProductoInexistenteError(
            f'El producto con id={sorted(faltantes)[0]} no existe.'
        )

    return productos


def _validar_precios(
    items: list[dict],
    productos: dict[int, Producto],
    cotizacion,
) -> None:
    """
    El precio de venta lo decide el servidor, no el navegador.

    Fuentes autorizadas para un producto:
      - su `precio_venta` vigente;
      - el precio de ese producto en la cotización que origina la venta
        (precio histórico legítimamente distinto del vigente).

    Cualquier otro valor se rechaza. Un precio distinto por catálogo
    desactualizado en la caja también cae acá: el mensaje pide recargar, que es
    la acción correcta.
    """
    precios_cotizacion: dict[int, set[Decimal]] = {}
    if cotizacion is not None:
        for detalle in cotizacion.detalles.all():
            precios_cotizacion.setdefault(detalle.producto_id, set()).add(
                _dinero(detalle.precio_unitario)
            )

    for item in items:
        producto = productos[item['producto_id']]
        autorizados = {_dinero(producto.precio_venta)}
        autorizados |= precios_cotizacion.get(item['producto_id'], set())

        if item['precio'] not in autorizados:
            raise PrecioNoAutorizadoError(
                f'El precio enviado para "{producto.nombre}" (${item["precio"]}) '
                f'no coincide con el precio vigente (${_dinero(producto.precio_venta)}). '
                f'Recargá el catálogo del POS y volvé a cargar la venta.'
            )


def _validar_stock(
    items: list[dict],
    productos: dict[int, Producto],
    *,
    permitir_negativo: bool,
) -> None:
    """
    Verifica stock disponible para el carrito.

    Agrega las cantidades POR PRODUCTO antes de comparar: con la validación
    línea por línea, un carrito con el mismo producto repetido dos veces pasaba
    ambas comprobaciones individuales y luego consumía más de lo disponible.
    """
    if permitir_negativo:
        return

    solicitado_por_producto: dict[int, int] = {}
    for item in items:
        solicitado_por_producto[item['producto_id']] = (
            solicitado_por_producto.get(item['producto_id'], 0) + item['cantidad']
        )

    for producto_id, cantidad_solicitada in solicitado_por_producto.items():
        producto = productos[producto_id]

        stock_disponible = Lote.objects.filter(
            producto=producto,
            cantidad_actual__gt=0,
            activo=True,
        ).aggregate(total=models.Sum('cantidad_actual'))['total'] or 0

        if cantidad_solicitada > stock_disponible:
            raise StockInsuficienteError(
                f'Stock insuficiente: {producto.nombre}. '
                f'Disponible: {stock_disponible}, solicitado: {cantidad_solicitada}.'
            )


# -----------------------------------------------------------------------------
# Cotización de origen
# -----------------------------------------------------------------------------

def _resolver_cotizacion(cotizacion_id):
    """
    Bloquea y valida la cotización que origina la venta, si la hay.

    Antes esto ocurría en un SEGUNDO request del navegador, después del commit
    de la venta: si ese request se perdía, la cotización quedaba PENDIENTE y
    podía convertirse otra vez, duplicando venta e inventario consumido.
    """
    if not cotizacion_id:
        return None

    from apps.cotizaciones.models import Cotizacion

    try:
        cotizacion = Cotizacion.objects.select_for_update().get(
            id=int(cotizacion_id)
        )
    except (TypeError, ValueError):
        raise CotizacionInvalidaError(
            f'Identificador de cotización inválido: {cotizacion_id!r}.'
        )
    except Cotizacion.DoesNotExist:
        raise CotizacionInvalidaError(
            f'La cotización id={cotizacion_id} no existe.'
        )

    if not cotizacion.puede_convertirse:
        raise CotizacionInvalidaError(
            f'La cotización {cotizacion.numero_cotizacion} ya no puede '
            f'convertirse (estado actual: {cotizacion.get_estado_display()}).'
        )

    return cotizacion


def _marcar_cotizacion_convertida(cotizacion, venta: Venta) -> None:
    """Vincula la cotización a la venta dentro de la misma transacción."""
    cotizacion.estado = 'CONVERTIDA'
    cotizacion.venta = venta
    cotizacion.save(update_fields=['estado', 'venta'])
    sync_events.evento_cotizacion_convertida(cotizacion)


# -----------------------------------------------------------------------------
# Cliente y precondiciones fiscales
# -----------------------------------------------------------------------------

def _resolver_cliente(cliente_id, *, condicion_pago: str):
    """Resuelve el cliente de la venta y aplica la regla de crédito."""
    cliente = None
    if cliente_id:
        from apps.clientes.models import Cliente
        try:
            cliente = Cliente.objects.get(id=cliente_id, activo=True)
        except (Cliente.DoesNotExist, ValueError, TypeError):
            # Comportamiento legacy: si el cliente no existe, queda
            # como contado (null). Se mantiene para no romper UI.
            cliente = None

    if condicion_pago == 'CREDITO':
        if cliente is None or not cliente.activo or cliente.es_contado:
            raise ClienteCreditoInvalidoError(
                'La venta a credito requiere un cliente real activo.'
            )

    return cliente


def _validar_precondiciones_ecf(*, tipo_ecf: str, cliente) -> None:
    """
    Aplica, antes del commit, las precondiciones deterministas del tipo fiscal.

    Reutiliza la regla de dominio del mapper (`venta_to_ecf`) para que la
    validación previa y la emisión no puedan divergir.
    """
    if tipo_ecf != '31':
        return

    from apps.facturacion_electronica.services.venta_to_ecf import (
        comprador_fiscal,
        motivo_tipo_31_no_emitible,
    )

    motivo = motivo_tipo_31_no_emitible(comprador_fiscal(cliente))
    if motivo:
        raise TipoECFInvalidoError(motivo)


# -----------------------------------------------------------------------------
# Persistencia
# -----------------------------------------------------------------------------

def _crear_venta(
    *,
    usuario: 'AbstractUser',
    items: list[dict],
    cliente,
    sucursal,
    total_esperado: Decimal,
    condicion_pago: str,
) -> Venta:
    """
    Crea la cabecera Venta. Calcula totales desde el carrito ya validado y los
    contrasta con `total_esperado`. El número de venta lo asigna el propio
    modelo en su save(), usando el prefijo de la sucursal recibida.
    """
    subtotal = sum(
        (item['precio'] * item['cantidad'] for item in items),
        Decimal('0.00'),
    )
    descuento_total = sum((item['descuento'] for item in items), Decimal('0.00'))
    total = subtotal - descuento_total

    if abs(total - total_esperado) > CENTAVO:
        raise TotalInconsistenteError(
            f'Total no coincide. Esperado: ${total_esperado}, '
            f'calculado desde carrito: ${total}.'
        )

    if total <= 0:
        raise TotalInconsistenteError(
            f'El total de la venta debe ser positivo. Calculado: ${total}.'
        )

    return Venta.objects.create(
        usuario=usuario,
        cliente=cliente,
        sucursal=sucursal,
        subtotal=subtotal,
        descuento_total=descuento_total,
        total=total,
        condicion_pago=condicion_pago,
        estado='COMPLETADA',
    )


def _crear_detalles_y_consumir_fifo(
    *,
    venta: Venta,
    items: list[dict],
    productos: dict[int, Producto],
    usuario: 'AbstractUser',
    permitir_negativo: bool,
) -> None:
    """
    Crea DetalleVenta por cada item del carrito, consume stock FIFO y persiste
    en el detalle el costo efectivamente consumido.

    DetalleVenta autocalcula subtotal/total_linea/descuento_porcentaje
    en su save(); solo pasamos los campos primarios.
    """
    for item in items:
        producto = productos[item['producto_id']]

        detalle = DetalleVenta.objects.create(
            venta=venta,
            producto=producto,
            cantidad=item['cantidad'],
            precio_unitario=item['precio'],
            descuento_monto=item['descuento'],
        )

        # Consumir stock FIFO. La función bloquea los lotes candidatos, así que
        # el descuento es seguro frente a otra caja vendiendo el mismo producto.
        resultado = procesar_venta_fifo(
            producto_id=producto.id,
            cantidad_solicitada=item['cantidad'],
            venta_id=venta.id,
            usuario=usuario,
        )

        if not resultado['success']:
            logger.warning(
                f'FIFO falló para venta={venta.numero_venta} '
                f'producto={producto.nombre}: {resultado.get("error")}'
            )

        faltante = resultado.get('cantidad_faltante', 0)
        if faltante > 0:
            if not permitir_negativo:
                # La validación previa vio stock suficiente, pero entre esa
                # lectura y el lock otra venta se llevó las unidades. Con
                # inventario negativo deshabilitado esto es un error de
                # negocio, no un warning: dejarlo pasar producía una venta
                # cuyo inventario, costo y movimientos no cuadran.
                raise StockInsuficienteError(
                    f'Stock insuficiente: {producto.nombre}. '
                    f'Faltaron {faltante} unidades al consumir el inventario. '
                    f'Revisá el stock y volvé a intentar.'
                )
            logger.warning(
                f'Stock insuficiente al consumir FIFO (inventario negativo '
                f'habilitado): venta={venta.numero_venta} producto={producto.nombre} '
                f'vendido={resultado.get("cantidad_vendida")} faltante={faltante}'
            )

        # Snapshot del costo consumido. Sin esto el detalle queda con el default
        # 0, el margen calculado es cero y ese cero se replica al cloud.
        detalle.costo_fifo = _dinero(resultado.get('costo_fifo', 0))
        detalle.save(update_fields=['costo_fifo'])


def _registrar_pagos(
    *,
    venta: Venta,
    metodo_pago: str,
    monto_efectivo: Decimal,
    monto_transferencia: Decimal,
    monto_tarjeta: Decimal,
    referencia_tarjeta: str,
    credito_data: dict[str, Any] | None = None,
) -> None:
    """
    Persiste los Pago de la venta. Para métodos puros, un solo Pago
    con monto = total. Para mixto, un Pago por método con monto > 0.

    El método ya viene validado contra `METODOS_PAGO_VALIDOS`; la rama final
    existe para que un método nuevo sin su rama de pago falle ruidosamente en
    vez de crear una venta sin ningún Pago.
    """
    numero = venta.numero_venta
    total = venta.total
    credito_data = credito_data or {}

    if metodo_pago == 'efectivo':
        Pago.objects.create(
            venta=venta,
            metodo='EFECTIVO',
            monto=total,
            referencia=f'Efectivo - {numero}',
        )
    elif metodo_pago == 'transferencia':
        Pago.objects.create(
            venta=venta,
            metodo='TRANSFERENCIA',
            monto=total,
            referencia=f'Transferencia - {numero}',
        )
    elif metodo_pago == 'tarjeta':
        Pago.objects.create(
            venta=venta,
            metodo='TARJETA',
            monto=total,
            referencia=f'Tarjeta {referencia_tarjeta} - {numero}',
        )
    elif metodo_pago == 'mixto':
        if monto_efectivo > 0:
            Pago.objects.create(
                venta=venta,
                metodo='EFECTIVO',
                monto=monto_efectivo,
                referencia=f'Efectivo (Mixto) - {numero}',
            )
        if monto_transferencia > 0:
            Pago.objects.create(
                venta=venta,
                metodo='TRANSFERENCIA',
                monto=monto_transferencia,
                referencia=f'Transferencia (Mixto) - {numero}',
            )
        if monto_tarjeta > 0:
            Pago.objects.create(
                venta=venta,
                metodo='TARJETA',
                monto=monto_tarjeta,
                referencia=f'Tarjeta (Mixto) {referencia_tarjeta} - {numero}',
            )
    elif metodo_pago == 'credito':
        monto_inicial = _decimal(credito_data.get('monto_inicial', 0))
        saldo_credito = total - monto_inicial
        metodo_inicial = (credito_data.get('metodo_inicial') or 'efectivo').strip()

        if monto_inicial > 0:
            if metodo_inicial == 'efectivo':
                Pago.objects.create(
                    venta=venta,
                    metodo='EFECTIVO',
                    monto=monto_inicial,
                    referencia=f'Inicial credito efectivo - {numero}',
                )
            elif metodo_inicial == 'transferencia':
                Pago.objects.create(
                    venta=venta,
                    metodo='TRANSFERENCIA',
                    monto=monto_inicial,
                    referencia=f'Inicial credito transferencia - {numero}',
                )
            elif metodo_inicial == 'tarjeta':
                Pago.objects.create(
                    venta=venta,
                    metodo='TARJETA',
                    monto=monto_inicial,
                    referencia=f'Inicial credito tarjeta {referencia_tarjeta} - {numero}',
                )
            elif metodo_inicial == 'mixto':
                inicial_efectivo = _decimal(credito_data.get('monto_efectivo', 0))
                inicial_transferencia = _decimal(credito_data.get('monto_transferencia', 0))
                inicial_tarjeta = _decimal(credito_data.get('monto_tarjeta', 0))
                if inicial_efectivo > 0:
                    Pago.objects.create(
                        venta=venta,
                        metodo='EFECTIVO',
                        monto=inicial_efectivo,
                        referencia=f'Inicial credito efectivo mixto - {numero}',
                    )
                if inicial_transferencia > 0:
                    Pago.objects.create(
                        venta=venta,
                        metodo='TRANSFERENCIA',
                        monto=inicial_transferencia,
                        referencia=f'Inicial credito transferencia mixto - {numero}',
                    )
                if inicial_tarjeta > 0:
                    Pago.objects.create(
                        venta=venta,
                        metodo='TARJETA',
                        monto=inicial_tarjeta,
                        referencia=f'Inicial credito tarjeta mixto {referencia_tarjeta} - {numero}',
                    )

        if saldo_credito > 0:
            Pago.objects.create(
                venta=venta,
                metodo='CREDITO',
                monto=saldo_credito,
                referencia=f'CxC - {numero}',
            )
    else:
        raise MetodoPagoInvalidoError(
            f'Método de pago "{metodo_pago}" sin forma de cobro definida.'
        )


def _validar_metodo_pago(
    metodo_pago: str,
    *,
    config,
    monto_efectivo: Decimal,
    monto_transferencia: Decimal,
    monto_tarjeta: Decimal,
    credito_data: dict[str, Any] | None,
) -> None:
    """
    Allowlist + habilitación por configuración del negocio.

    El POS ya filtra los métodos que muestra, pero ese filtro es de interfaz:
    un POST directo con `metodo_pago="otro"` caía por todas las ramas de
    `_registrar_pagos` sin crear ningún Pago y devolvía éxito.
    """
    if metodo_pago not in METODOS_PAGO_VALIDOS:
        raise MetodoPagoInvalidoError(
            f'Método de pago "{metodo_pago}" no reconocido. '
            f'Válidos: {", ".join(METODOS_PAGO_VALIDOS)}.'
        )

    def _habilitado(simple: str) -> bool:
        return bool(getattr(config, FLAG_CONFIG_POR_METODO[simple], False))

    if metodo_pago in FLAG_CONFIG_POR_METODO:
        if not _habilitado(metodo_pago):
            raise MetodoPagoInvalidoError(
                f'El método de pago "{metodo_pago}" está deshabilitado en la '
                f'configuración del negocio.'
            )
        return

    if metodo_pago == 'mixto':
        componentes = (
            ('efectivo', monto_efectivo),
            ('transferencia', monto_transferencia),
            ('tarjeta', monto_tarjeta),
        )
        usados = [nombre for nombre, monto in componentes if monto > 0]
        if not usados:
            raise MetodoPagoInvalidoError(
                'El pago mixto no trae ningún monto mayor que cero.'
            )
        for nombre in usados:
            if not _habilitado(nombre):
                raise MetodoPagoInvalidoError(
                    f'El método de pago "{nombre}" (usado en el pago mixto) está '
                    f'deshabilitado en la configuración del negocio.'
                )
        return

    # 'credito': el inicial, si existe, se cobra por un método simple que
    # también debe estar habilitado. El gate del módulo de CxC va aparte.
    credito_data = credito_data or {}
    if _decimal(credito_data.get('monto_inicial', 0)) > 0:
        metodo_inicial = (credito_data.get('metodo_inicial') or 'efectivo').strip()
        simples = (
            ('efectivo', 'transferencia', 'tarjeta')
            if metodo_inicial == 'mixto'
            else (metodo_inicial,)
        )
        for nombre in simples:
            if nombre in FLAG_CONFIG_POR_METODO and not _habilitado(nombre):
                raise MetodoPagoInvalidoError(
                    f'El método "{nombre}" del inicial de crédito está '
                    f'deshabilitado en la configuración del negocio.'
                )


def _verificar_pagos(venta: Venta) -> None:
    """
    Postcondición de caja: lo registrado como cobro tiene que sumar el total.

    Cierra el hueco de una venta que suma a ingresos pero no aparece con forma
    de cobro en el cierre de caja.
    """
    total_pagado = venta.pagos.aggregate(
        total=models.Sum('monto')
    )['total'] or Decimal('0.00')

    if abs(_dinero(total_pagado) - _dinero(venta.total)) > CENTAVO:
        raise PagosInconsistentesError(
            f'Los pagos registrados (${_dinero(total_pagado)}) no suman el total '
            f'de la venta (${_dinero(venta.total)}).'
        )


def _hook_imprimir_ticket(venta: Venta, usuario) -> None:
    """
    Hook post-commit: imprime el ticket y DEJA RASTRO del resultado.

    El auto-print es best-effort (no debe tumbar la venta), pero antes el
    resultado se descartaba y un fallo quedaba invisible. Aqui capturamos
    cualquier excepcion y, si la impresion no fue exitosa (incluido el caso
    'DISABLED'), lo registramos en log. La auditoria la hace el print_manager.
    """
    try:
        resultado = print_manager.print_ticket_venta(
            venta=venta, usuario=usuario, reimpresion=False,
        )
    except Exception:
        logger.exception(
            'Fallo no controlado al imprimir ticket de venta %s', venta.numero_venta
        )
        return
    if not resultado.get('success'):
        logger.warning(
            'No se imprimio el ticket de venta %s: %s',
            venta.numero_venta,
            resultado.get('error') or resultado.get('mensaje'),
        )


def _hook_encolar_ecf(venta: Venta, tipo_ecf: str) -> None:
    """
    Hook que corre post-commit. Crea el ECF en estado PENDIENTE y
    registra el evento inicial. NO llama a MSeller — eso lo hace el
    management command ecf_procesar_pendientes.

    Si algo falla aquí, NO se hace rollback de la venta (estamos
    post-commit). Se loguea el error para que el operador lo investigue.
    Las precondiciones deterministas del tipo fiscal se validan antes del
    commit, en `_validar_precondiciones_ecf`.
    """
    try:
        from apps.facturacion_electronica.services.cola_emision import (
            encolar_emision,
        )
        encolar_emision(venta=venta, tipo_ecf=tipo_ecf)
    except Exception as exc:
        # Logging detallado pero no relanzamos: la venta ya commiteó.
        # El ECF se puede crear manualmente o por otro mecanismo si
        # el operador lo nota.
        logger.exception(
            f'Falló encolar e-CF para venta={venta.numero_venta} '
            f'tipo={tipo_ecf}: {exc}'
        )
