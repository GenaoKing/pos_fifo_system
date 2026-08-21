from django.db import models
from django.db.models import Q
from django.utils import timezone
from decimal import Decimal
from django.conf import settings

BORRADOR = 'BORRADOR'
FINAL = 'FINAL'
ESTADOS_CIERRE = [
    (BORRADOR, 'Borrador (se recalcula)'),
    (FINAL, 'Final (congelado)'),
]


class CierreCaja(models.Model):
    """
    Resumen diario de ventas y cobros.

    NO es una conciliacion de caja fisica (RPT-008). Agrega ventas completadas,
    pagos, cobros de cartera y anulaciones de una fecha; el arqueo real —fondo
    de apertura, efectivo contado, retiros, diferencia— vive en
    `apps.caja.TurnoCaja`. Llamarlo "cierre de caja" invitaba a leerlo como una
    conciliacion que este documento nunca hizo.

    Para que el lector pueda distinguirlo de un vistazo, el resumen incorpora
    los indicadores del arqueo del dia (`turnos_cerrados`, `turnos_abiertos`,
    `diferencia_arqueo`) SIN mezclarlos con la facturacion: dicen si el dia
    quedo conciliado, no cuanto se vendio.

    Ciclo de vida (RPT-004): nace BORRADOR y se recalcula en cada generacion
    mientras siga en ese estado. Solo `finalizar()` lo congela. Antes el primer
    calculo del dia quedaba fijo para siempre: una venta tardia, una anulacion o
    una reversa posterior jamas lo tocaban, y reintentar el comando devolvia el
    mismo total con apariencia de idempotencia.
    """
    # Fecha del cierre
    fecha = models.DateField(
        help_text="Fecha del día que se está cerrando"
    )

    sucursal = models.ForeignKey(
        'sucursales.Sucursal',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='cierres_diarios',
        help_text=(
            "Sucursal del resumen. Null = consolidado de todas, que solo "
            "puede generar quien tiene alcance global."
        ),
    )
    
    # Totales de ventas
    cantidad_ventas = models.IntegerField(
        default=0,
        help_text="Número de ventas del día"
    )

    total_ventas = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Total vendido en el día"
    )

    total_descuentos = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Total de descuentos aplicados"
    )

    # Desglose por método de pago
    total_efectivo = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00')
    )

    total_transferencia = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00')
    )

    total_tarjeta = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00')
    )

    total_cobros_cxc = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Total cobrado de cuentas por cobrar en el dia"
    )

    # Anulaciones
    cantidad_anulaciones = models.IntegerField(
        default=0,
        help_text="Ventas anuladas en el día"
    )

    total_anulaciones = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00')
    )

    # Desglose por cajero
    resumen_cajeros = models.JSONField(
        default=dict,
        help_text="Detalle de ventas por cajero"
    )

    # Metadatos
    generado_automaticamente = models.BooleanField(
        default=False,
        help_text="True si fue generado por tarea automática"
    )

    generado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="Usuario que generó el cierre (null si fue automático)"
    )

    fecha_generacion = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp de cuando se generó el reporte"
    )

    # Archivo PDF
    archivo_pdf = models.CharField(
        max_length=500,
        null=True,
        blank=True,
        help_text="Ruta del PDF generado"
    )

    # Conciliacion de caja fisica (viene de apps.caja, NO se mezcla con la
    # facturacion de arriba).
    turnos_cerrados = models.IntegerField(
        default=0,
        help_text="Turnos de caja cerrados en la fecha",
    )

    turnos_abiertos = models.IntegerField(
        default=0,
        help_text=(
            "Turnos que seguian abiertos al calcular. Mayor que cero significa "
            "que el dia no esta conciliado."
        ),
    )

    diferencia_arqueo = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Suma de faltantes/sobrantes de los turnos cerrados",
    )

    # Control del ciclo de vida
    estado = models.CharField(
        max_length=10,
        choices=ESTADOS_CIERRE,
        default=BORRADOR,
        help_text="BORRADOR se recalcula; FINAL queda congelado",
    )

    version = models.PositiveIntegerField(
        default=1,
        help_text="Se incrementa en cada recalculo",
    )

    fecha_calculo = models.DateTimeField(
        auto_now=True,
        help_text="Ultima vez que se recalcularon las cifras",
    )

    class Meta:
        db_table = 'cierres_caja'
        ordering = ['-fecha']
        verbose_name = 'Resumen Diario de Ventas y Cobros'
        verbose_name_plural = 'Resumenes Diarios de Ventas y Cobros'
        constraints = [
            # Dos indices parciales en vez de `unique_together`: en Postgres los
            # NULL no colisionan entre si, asi que una unicidad sobre
            # (fecha, sucursal) dejaria crear N consolidados para la misma
            # fecha — justo el caso que hay que impedir.
            models.UniqueConstraint(
                fields=['fecha'],
                condition=Q(sucursal__isnull=True),
                name='cierre_unico_consolidado_por_fecha',
            ),
            models.UniqueConstraint(
                fields=['fecha', 'sucursal'],
                condition=Q(sucursal__isnull=False),
                name='cierre_unico_por_fecha_y_sucursal',
            ),
        ]

    def __str__(self):
        ambito = self.sucursal.codigo if self.sucursal_id else 'consolidado'
        return f"Resumen {self.fecha} ({ambito}) - ${self.total_ventas}"

    @property
    def es_final(self):
        return self.estado == FINAL

    @property
    def conciliado(self):
        """True si no quedaron turnos abiertos al momento del calculo."""
        return self.turnos_abiertos == 0

    def finalizar(self, usuario=None):
        """Congela el resumen. A partir de aca no se recalcula solo."""
        self.estado = FINAL
        if usuario is not None:
            self.generado_por = usuario
        self.save(update_fields=['estado', 'generado_por', 'fecha_calculo'])
        return self


class TopProducto(models.Model):
    """
    Snapshot de productos más vendidos por período
    """
    # Período
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()

    # Producto
    producto = models.ForeignKey(
        'productos.Producto',
        on_delete=models.PROTECT
    )

    # Estadísticas
    cantidad_vendida = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Total de unidades vendidas"
    )

    total_ventas = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Total en dinero generado"
    )

    numero_transacciones = models.IntegerField(
        help_text="Número de ventas que incluyen este producto"
    )

    margen_promedio = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        help_text=(
            "Margen ponderado del periodo: (total_linea - costo_fifo) / "
            "total_linea * 100. Antes se persistia un 25.0 fijo de placeholder."
        ),
    )

    costo_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Costo FIFO acumulado de las lineas del periodo",
    )

    sucursal = models.ForeignKey(
        'sucursales.Sucursal',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='top_productos',
        help_text="Null = consolidado de todas las sucursales",
    )

    # Metadatos
    fecha_generacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'top_productos'
        ordering = ['-cantidad_vendida']
        indexes = [
            models.Index(fields=['fecha_inicio', 'fecha_fin']),
        ]
        verbose_name = 'Top Producto'
        verbose_name_plural = 'Top Productos'
        constraints = [
            models.UniqueConstraint(
                fields=['fecha_inicio', 'fecha_fin', 'producto'],
                condition=Q(sucursal__isnull=True),
                name='top_unico_consolidado_por_periodo',
            ),
            models.UniqueConstraint(
                fields=['fecha_inicio', 'fecha_fin', 'producto', 'sucursal'],
                condition=Q(sucursal__isnull=False),
                name='top_unico_por_periodo_y_sucursal',
            ),
        ]

    def __str__(self):
        return f"{self.producto.nombre} - {self.cantidad_vendida} unidades"


class InventarioValorizado(models.Model):
    """
    Snapshot de inventario valorizado a un instante de corte REAL.

    El contrato anterior era enganoso (RPT-002): el endpoint aceptaba cualquier
    fecha —pasada o futura— y devolvia el stock de AHORA rotulado con esa fecha.
    Un corte etiquetado 2020-01-01 mostraba lotes creados esta semana, y una
    fecha futura se aceptaba y persistia.

    Ahora `momento_corte` guarda el instante que el snapshot representa de
    verdad, y las cantidades se reconstruyen desde `MovimientoLote` cuando el
    corte es anterior a ahora.
    """
    # Fecha del snapshot
    fecha = models.DateField(
        help_text="Fecha del inventario"
    )

    momento_corte = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Instante exacto que representa el snapshot. Para una fecha pasada "
            "es el fin de ese dia; para hoy, el momento de generacion."
        ),
    )

    sucursal = models.ForeignKey(
        'sucursales.Sucursal',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='inventarios_valorizados',
        help_text="Null = consolidado de todas las sucursales",
    )
    
    # Datos del inventario
    datos_inventario = models.JSONField(
        help_text="Array de productos con lotes y valores FIFO"
    )

    # Totales
    total_productos = models.IntegerField(
        help_text="Cantidad de productos diferentes"
    )

    total_unidades = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Total de unidades en stock"
    )

    valor_total_inventario = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text="Valor total del inventario según FIFO"
    )

    # Metadatos
    fecha_generacion = models.DateTimeField(auto_now_add=True)

    archivo_pdf = models.CharField(
        max_length=500,
        null=True,
        blank=True
    )

    class Meta:
        db_table = 'inventarios_valorizados'
        ordering = ['-fecha']
        verbose_name = 'Inventario Valorizado'
        verbose_name_plural = 'Inventarios Valorizados'
        constraints = [
            models.UniqueConstraint(
                fields=['fecha'],
                condition=Q(sucursal__isnull=True),
                name='inventario_unico_consolidado_por_fecha',
            ),
            models.UniqueConstraint(
                fields=['fecha', 'sucursal'],
                condition=Q(sucursal__isnull=False),
                name='inventario_unico_por_fecha_y_sucursal',
            ),
        ]

    def __str__(self):
        return f"Inventario {self.fecha} - ${self.valor_total_inventario}"
