"""
apps/reportes/report_manager.py

Generacion de los reportes persistidos: resumen diario, top de productos e
inventario valorizado.

Tres reglas que antes no se cumplian y que este modulo ahora sostiene:

1. **La transaccion se abre donde se escribe** (RPT-006). Los generadores se
   decoraban con `@transaction.atomic` sin `using`, lo que abre la transaccion
   en `default`. Bajo DB-per-tenant el router manda los modelos de negocio al
   alias del tenant, asi que las escrituras ocurrian FUERA de la transaccion:
   el delete+recreate del top podia quedar a medias pareciendo atomico.

2. **Un reporte se recalcula mientras sea borrador** (RPT-004). El primer
   calculo del dia ya no queda congelado por accidente.

3. **Una fecha de corte pasada significa el pasado** (RPT-002). El inventario
   se reconstruye desde el ledger de movimientos, no se disfraza el stock
   actual con una etiqueta vieja.
"""
import logging
from datetime import datetime, time
from decimal import Decimal

from django.db import router, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.cuentas_por_cobrar.models import PagoCxC
from apps.inventario.models import Lote, MovimientoLote
from apps.productos.models import Producto
from apps.ventas.models import DetalleVenta, Pago, Venta

from .models import BORRADOR, FINAL, CierreCaja, InventarioValorizado, TopProducto

logger = logging.getLogger('reportes')

CERO = Decimal('0.00')


class FechaFuturaError(ValueError):
    """Se pidio un corte que todavia no ocurrio."""


class CierreFinalizadoError(RuntimeError):
    """Se intento recalcular un resumen ya congelado sin pedirlo explicitamente."""


def _alias():
    """Alias de BD donde realmente se escriben los modelos de negocio."""
    return router.db_for_write(CierreCaja)


def _atomic():
    """`transaction.atomic` sobre la base correcta, no sobre `default`."""
    return transaction.atomic(using=_alias())


def _fin_del_dia(fecha):
    """Instante de corte de una fecha: su ultimo microsegundo, en hora local."""
    naive = datetime.combine(fecha, time.max)
    tz = timezone.get_current_timezone()
    return timezone.make_aware(naive, tz)


def _momento_corte(fecha):
    """
    Instante que representa un snapshot de `fecha`.

    Para hoy es "ahora" (el inventario actual); para una fecha pasada, el final
    de ese dia. Una fecha futura no tiene corte posible.
    """
    hoy = timezone.localdate()
    if fecha > hoy:
        raise FechaFuturaError(
            f'No se puede cortar inventario al {fecha}: es una fecha futura.'
        )
    if fecha == hoy:
        return timezone.now()
    return _fin_del_dia(fecha)


def _acotar(queryset, sucursal, campo='sucursal'):
    """Filtra por sucursal; `None` significa consolidado (sin filtro)."""
    if sucursal is None:
        return queryset
    return queryset.filter(**{campo: sucursal})


class ReporteManager:
    """Maneja la generación de todos los reportes del sistema"""

    # ------------------------------------------------------------------
    # RESUMEN DIARIO DE VENTAS Y COBROS
    # ------------------------------------------------------------------

    @staticmethod
    def generar_cierre_diario(
        fecha=None,
        generado_automaticamente=False,
        usuario=None,
        sucursal=None,
        forzar=False,
    ):
        """
        Calcula (o recalcula) el resumen diario de `fecha`.

        - Si no existe, lo crea en estado BORRADOR.
        - Si existe y sigue BORRADOR, **lo recalcula** y sube `version`.
        - Si ya es FINAL, lo devuelve intacto salvo que `forzar=True`, que
          recalcula y versiona dejando traza.

        Antes devolvia el registro existente sin mirar nada mas: una venta
        tardia, una anulacion o un pago reversado nunca llegaban al resumen, y
        el reintento del comando parecia idempotente cuando en realidad estaba
        sirviendo datos obsoletos.
        """
        if fecha is None:
            fecha = timezone.localdate()

        if fecha > timezone.localdate():
            raise FechaFuturaError(
                f'No se puede resumir el {fecha}: es una fecha futura.'
            )

        with _atomic():
            existente = CierreCaja.objects.select_for_update().filter(
                fecha=fecha, sucursal=sucursal,
            ).first()

            if existente is not None and existente.estado == FINAL and not forzar:
                return existente

            cifras = ReporteManager._calcular_cifras_del_dia(fecha, sucursal)

            if existente is None:
                return CierreCaja.objects.create(
                    fecha=fecha,
                    sucursal=sucursal,
                    generado_automaticamente=generado_automaticamente,
                    generado_por=usuario,
                    estado=BORRADOR,
                    version=1,
                    **cifras,
                )

            for campo, valor in cifras.items():
                setattr(existente, campo, valor)
            existente.version += 1
            existente.generado_automaticamente = generado_automaticamente
            if usuario is not None:
                existente.generado_por = usuario
            existente.save()
            return existente

    @staticmethod
    def _calcular_cifras_del_dia(fecha, sucursal=None):
        """Todas las cifras del resumen, en un dict listo para asignar."""
        ventas = _acotar(
            Venta.objects.filter(fecha_venta__date=fecha, estado='COMPLETADA'),
            sucursal,
        )

        totales_ventas = ventas.aggregate(
            cantidad=Count('id'),
            total=Sum('total'),
            descuentos=Sum('descuento_total'),
        )

        # Un solo GROUP BY en vez de tres agregaciones separadas sobre el mismo
        # conjunto de pagos.
        por_metodo = {
            fila['metodo']: fila['total'] or CERO
            for fila in Pago.objects.filter(venta__in=ventas)
            .values('metodo')
            .annotate(total=Sum('monto'))
        }

        cobros_cxc = _acotar(
            PagoCxC.objects.filter(fecha_pago__date=fecha, estado='APLICADO'),
            sucursal,
            campo='cuenta__sucursal',
        ).aggregate(total=Sum('monto'))['total'] or CERO

        anulaciones = _acotar(
            Venta.objects.filter(fecha_anulacion__date=fecha, estado='ANULADA'),
            sucursal,
        ).aggregate(cantidad=Count('id'), total=Sum('total'))

        return {
            'cantidad_ventas': totales_ventas['cantidad'] or 0,
            'total_ventas': totales_ventas['total'] or CERO,
            'total_descuentos': totales_ventas['descuentos'] or CERO,
            'total_efectivo': por_metodo.get('EFECTIVO', CERO),
            'total_transferencia': por_metodo.get('TRANSFERENCIA', CERO),
            'total_tarjeta': por_metodo.get('TARJETA', CERO),
            'total_cobros_cxc': cobros_cxc,
            'cantidad_anulaciones': anulaciones['cantidad'] or 0,
            'total_anulaciones': anulaciones['total'] or CERO,
            'resumen_cajeros': ReporteManager._resumen_por_cajero(ventas),
            **ReporteManager._arqueo_del_dia(fecha, sucursal),
        }

    @staticmethod
    def _resumen_por_cajero(ventas):
        """
        Ventas agrupadas por cajero.

        La clave es el USERNAME, no el id (RPT-015): el PDF imprimia la clave
        como "Cajero" y salian numeros internos donde el lector espera nombres.
        El nombre completo sigue viajando en `nombre`.
        """
        filas = ventas.values(
            'usuario__id', 'usuario__username',
            'usuario__first_name', 'usuario__last_name',
        ).annotate(cantidad=Count('id'), total=Sum('total'))

        resumen = {}
        for fila in filas:
            username = fila['usuario__username'] or f"usuario-{fila['usuario__id']}"
            nombre = ' '.join(
                p for p in (fila['usuario__first_name'], fila['usuario__last_name']) if p
            ).strip()
            resumen[username] = {
                'usuario_id': fila['usuario__id'],
                'nombre': nombre or username,
                'cantidad': fila['cantidad'],
                'total': str(fila['total'] or CERO),
            }
        return resumen

    @staticmethod
    def _arqueo_del_dia(fecha, sucursal=None):
        """
        Indicadores del arqueo fisico (RPT-008).

        No se mezclan con la facturacion: responden "¿quedo conciliado el dia?",
        que es justo lo que un documento llamado "cierre de caja" no podia
        responder porque nunca miraba `apps.caja`.
        """
        from apps.caja.models import TurnoCaja

        turnos = TurnoCaja.objects.filter(fecha_apertura__date=fecha)
        if sucursal is not None:
            turnos = turnos.filter(caja__sucursal=sucursal)

        agregado = turnos.aggregate(
            cerrados=Count('id', filter=Q(estado='CERRADO')),
            abiertos=Count('id', filter=Q(estado='ABIERTO')),
            diferencia=Sum('diferencia', filter=Q(estado='CERRADO')),
        )
        return {
            'turnos_cerrados': agregado['cerrados'] or 0,
            'turnos_abiertos': agregado['abiertos'] or 0,
            'diferencia_arqueo': agregado['diferencia'] or CERO,
        }

    # ------------------------------------------------------------------
    # TOP DE PRODUCTOS
    # ------------------------------------------------------------------

    @staticmethod
    def generar_top_productos(fecha_inicio, fecha_fin, limite=10, sucursal=None):
        """
        Ranking de productos mas vendidos del periodo.

        Dos bugs corregidos:

        - Agregaba `Sum('total')`, campo que no existe en `DetalleVenta` (es
          `total_linea`). El generador lanzaba `FieldError` SIEMPRE, y el
          endpoint lo silenciaba: la respuesta decia `success=true` y la tabla
          quedaba vacia.
        - Persistia `margen_promedio = 25.0`, un placeholder. Arreglar solo el
          nombre del campo habria empezado a guardar un margen inventado, que
          es peor que el fallo visible. Ahora se calcula ponderado contra el
          `costo_fifo` real de cada linea.
        """
        with _atomic():
            TopProducto.objects.filter(
                fecha_inicio=fecha_inicio, fecha_fin=fecha_fin, sucursal=sucursal,
            ).delete()

            ventas = _acotar(
                Venta.objects.filter(
                    fecha_venta__date__gte=fecha_inicio,
                    fecha_venta__date__lte=fecha_fin,
                    estado='COMPLETADA',
                ),
                sucursal,
            )

            detalles = DetalleVenta.objects.filter(venta__in=ventas).values(
                'producto',
            ).annotate(
                cantidad_vendida=Sum('cantidad'),
                total_ventas=Sum('total_linea'),
                costo_total=Sum('costo_fifo'),
                numero_transacciones=Count('venta', distinct=True),
            ).order_by('-cantidad_vendida')[:limite]

            detalles = list(detalles)
            productos = Producto.objects.in_bulk(
                [d['producto'] for d in detalles]
            )

            top_productos = []
            for detalle in detalles:
                producto = productos.get(detalle['producto'])
                if producto is None:
                    continue

                ingreso = detalle['total_ventas'] or CERO
                costo = detalle['costo_total'] or CERO
                top_productos.append(TopProducto(
                    fecha_inicio=fecha_inicio,
                    fecha_fin=fecha_fin,
                    sucursal=sucursal,
                    producto=producto,
                    cantidad_vendida=detalle['cantidad_vendida'],
                    total_ventas=ingreso,
                    costo_total=costo,
                    numero_transacciones=detalle['numero_transacciones'],
                    margen_promedio=ReporteManager._margen(ingreso, costo),
                ))

            TopProducto.objects.bulk_create(top_productos)
            return top_productos

    @staticmethod
    def _margen(ingreso, costo):
        """Margen ponderado en %, acotado al rango que admite la columna."""
        if not ingreso:
            return CERO
        margen = (ingreso - costo) / ingreso * Decimal('100')
        margen = max(Decimal('-9999.99'), min(Decimal('9999.99'), margen))
        return margen.quantize(Decimal('0.01'))

    # ------------------------------------------------------------------
    # INVENTARIO VALORIZADO
    # ------------------------------------------------------------------

    @staticmethod
    def generar_inventario_valorizado(fecha=None, sucursal=None, recalcular=False):
        """
        Snapshot del inventario valorizado a un corte REAL.

        Si `fecha` es anterior a hoy, las cantidades se reconstruyen desde
        `MovimientoLote`; si es hoy, se lee el stock actual. Una fecha futura se
        rechaza. Antes cualquier fecha devolvia el stock de ahora con la
        etiqueta pedida, y `2099-12-31` se aceptaba y persistia.
        """
        if fecha is None:
            fecha = timezone.localdate()

        corte = _momento_corte(fecha)

        with _atomic():
            existente = InventarioValorizado.objects.filter(
                fecha=fecha, sucursal=sucursal,
            ).first()
            if existente is not None and not recalcular:
                return existente

            datos, total_unidades, valor_total = ReporteManager._componer_inventario(
                corte, sucursal, historico=fecha < timezone.localdate(),
            )

            campos = {
                'momento_corte': corte,
                'datos_inventario': datos,
                'total_productos': len(datos),
                'total_unidades': total_unidades,
                'valor_total_inventario': valor_total,
            }

            if existente is not None:
                for campo, valor in campos.items():
                    setattr(existente, campo, valor)
                existente.save()
                return existente

            return InventarioValorizado.objects.create(
                fecha=fecha, sucursal=sucursal, **campos,
            )

    @staticmethod
    def _componer_inventario(corte, sucursal, historico):
        """
        Arma la estructura del snapshot.

        `historico=False` usa `cantidad_actual`; `True` reconstruye la cantidad
        de cada lote al instante de corte leyendo su ultimo movimiento anterior.
        """
        lotes = Lote.objects.filter(activo=True).select_related('producto')
        lotes = _acotar(lotes, sucursal)

        if historico:
            # Un lote creado despues del corte no existia entonces.
            lotes = lotes.filter(fecha_creacion__lte=corte)
            cantidades = ReporteManager._cantidades_al_corte(lotes, corte)
        else:
            lotes = lotes.filter(cantidad_actual__gt=0)
            cantidades = None

        # Una sola pasada, ordenada por producto: sin consulta por producto.
        por_producto = {}
        for lote in lotes.order_by('producto__nombre', 'fecha_compra', 'id'):
            cantidad = (
                cantidades.get(lote.pk, CERO) if cantidades is not None
                else lote.cantidad_actual
            )
            if cantidad <= 0:
                continue

            entrada = por_producto.setdefault(lote.producto_id, {
                'producto_id': lote.producto_id,
                'nombre': lote.producto.nombre,
                'sku': lote.producto.sku,
                'cantidad_total': CERO,
                'valor_total': CERO,
                'lotes': [],
            })
            valor = cantidad * lote.costo_unitario
            entrada['cantidad_total'] += cantidad
            entrada['valor_total'] += valor
            entrada['lotes'].append({
                'numero_lote': lote.numero_lote,
                'cantidad': str(cantidad),
                'costo_unitario': str(lote.costo_unitario),
                'valor_lote': str(valor),
                'fecha_compra': (
                    lote.fecha_compra.isoformat() if lote.fecha_compra else None
                ),
            })

        datos = []
        total_unidades = CERO
        valor_total = CERO
        for entrada in por_producto.values():
            cantidad = entrada['cantidad_total']
            valor = entrada['valor_total']
            datos.append({
                'producto_id': entrada['producto_id'],
                'nombre': entrada['nombre'],
                'sku': entrada['sku'],
                'cantidad_total': str(cantidad),
                'costo_promedio_fifo': str(
                    (valor / cantidad).quantize(Decimal('0.01')) if cantidad else CERO
                ),
                'valor_total': str(valor),
                'lotes': entrada['lotes'],
            })
            total_unidades += cantidad
            valor_total += valor

        return datos, total_unidades, valor_total

    @staticmethod
    def _cantidades_al_corte(lotes, corte):
        """
        Cantidad de cada lote en `corte`, reconstruida desde el ledger.

        `MovimientoLote` guarda `cantidad_nueva` en cada mutacion, y toda via
        que toca stock —compra, venta, ajuste, anulacion, merma, dano— escribe
        uno. El estado a una fecha es entonces el `cantidad_nueva` del ultimo
        movimiento anterior al corte. Un lote sin movimientos previos conserva
        su `cantidad_inicial`.
        """
        lote_ids = list(lotes.values_list('pk', flat=True))
        if not lote_ids:
            return {}

        cantidades = dict(
            Lote.objects.filter(pk__in=lote_ids).values_list('pk', 'cantidad_inicial')
        )

        # Orden ascendente y sobreescritura: la ultima asignacion de cada lote
        # es su movimiento mas reciente antes del corte.
        movimientos = MovimientoLote.objects.filter(
            lote_id__in=lote_ids, fecha_creacion__lte=corte,
        ).order_by('fecha_creacion', 'id').values_list('lote_id', 'cantidad_nueva')

        for lote_id, cantidad_nueva in movimientos.iterator(chunk_size=2000):
            cantidades[lote_id] = cantidad_nueva

        return cantidades
