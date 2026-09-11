from django.db import models
from django.core.validators import MinValueValidator
from django.conf import settings
from decimal import Decimal
from django.utils import timezone
import pytz


class Cotizacion(models.Model):
    """
    Cotizacion de productos.
    NO afecta inventario. Es solo un snapshot de precios.
    Se puede convertir a venta para no re-buscar productos.
    """
    ESTADOS = [
        ('PENDIENTE', 'Pendiente'),
        ('CONVERTIDA', 'Convertida a Venta'),
    ]

    numero_cotizacion = models.CharField(
        max_length=50,
        verbose_name='Numero de Cotizacion'
    )

    cliente = models.ForeignKey(
        'clientes.Cliente',
        on_delete=models.PROTECT,
        related_name='cotizaciones',
        verbose_name='Cliente'
    )

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='cotizaciones',
        verbose_name='Creado por'
    )

    sucursal = models.ForeignKey(
        'sucursales.Sucursal',
        on_delete=models.PROTECT,
        related_name='cotizaciones',
        verbose_name='Sucursal',
        blank=True,
        null=True,
        help_text='Sucursal donde se creo la cotizacion. Null para cotizaciones legacy.'
    )

    fecha_creacion = models.DateTimeField(
        verbose_name='Fecha de Creacion'
    )

    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Subtotal'
    )

    descuento_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Descuento Total'
    )

    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Total'
    )

    estado = models.CharField(
        max_length=20,
        choices=ESTADOS,
        default='PENDIENTE',
        verbose_name='Estado'
    )

    # Referencia a la venta si fue convertida.
    #
    # COT-015: `on_delete=PROTECT` (antes `SET_NULL`). Una cotizacion CONVERTIDA
    # DEBE conservar el vinculo a la venta que la consumio — es la invariante que
    # el CheckConstraint `cotizacion_convertida_exige_venta` exige a nivel de BD.
    # Con SET_NULL, borrar la venta dejaba la cotizacion CONVERTIDA con
    # `venta=NULL` (violando esa invariante). Las ventas se ANULAN, no se borran;
    # PROTECT convierte un borrado accidental en un error claro en vez de
    # orfandad silenciosa.
    venta = models.OneToOneField(
        'ventas.Venta',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='cotizacion_origen',
        verbose_name='Venta Generada'
    )

    notas = models.TextField(
        blank=True,
        null=True,
        verbose_name='Notas'
    )

    class Meta:
        verbose_name = 'Cotizacion'
        verbose_name_plural = 'Cotizaciones'
        ordering = ['-fecha_creacion']
        indexes = [
            models.Index(fields=['numero_cotizacion']),
            models.Index(fields=['fecha_creacion']),
            models.Index(fields=['estado']),
            models.Index(fields=['sucursal', 'estado']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['sucursal', 'numero_cotizacion'],
                name='unique_cotizacion_por_sucursal_numero',
            ),
            # COT-008: importes imposibles no persistibles ni por escritura directa.
            models.CheckConstraint(
                condition=models.Q(subtotal__gte=Decimal('0.00')),
                name='cotizacion_subtotal_no_negativo',
            ),
            models.CheckConstraint(
                condition=models.Q(descuento_total__gte=Decimal('0.00')),
                name='cotizacion_descuento_no_negativo',
            ),
            models.CheckConstraint(
                condition=models.Q(total__gte=Decimal('0.00')),
                name='cotizacion_total_no_negativo',
            ),
            # COT-015: una cotizacion CONVERTIDA exige el vinculo a su venta.
            models.CheckConstraint(
                condition=(
                    ~models.Q(estado='CONVERTIDA') | models.Q(venta__isnull=False)
                ),
                name='cotizacion_convertida_exige_venta',
            ),
        ]

    def __str__(self):
        return f"Cotizacion {self.numero_cotizacion} - ${self.total}"

    def save(self, *args, **kwargs):
        if not self.pk and not self.fecha_creacion:
            santo_domingo_tz = pytz.timezone('America/Santo_Domingo')
            self.fecha_creacion = timezone.now().astimezone(santo_domingo_tz)

        if not self.numero_cotizacion:
            fecha_str = self.fecha_creacion.strftime('%Y%m%d')
            prefijo = f'{self.sucursal.codigo}-COT-{fecha_str}' if self.sucursal else f'COT-{fecha_str}'
            ultimo = Cotizacion.objects.filter(
                sucursal=self.sucursal,
                numero_cotizacion__startswith=prefijo
            ).count()
            self.numero_cotizacion = f'{prefijo}-{str(ultimo + 1).zfill(5)}'

        super().save(*args, **kwargs)

    def calcular_totales(self):
        """Recalcula totales basado en detalles"""
        detalles = self.detalles.all()
        self.subtotal = sum(d.subtotal for d in detalles)
        self.descuento_total = sum(d.descuento_monto for d in detalles)
        self.total = sum(d.total_linea for d in detalles)
        return {
            'subtotal': self.subtotal,
            'descuento_total': self.descuento_total,
            'total': self.total
        }

    @property
    def puede_convertirse(self):
        """Pendiente y dentro de la vigencia que el propio PDF afirma."""
        return self.estado == 'PENDIENTE' and not self.esta_vencida

    # El PDF dice "valida por 15 dias" desde siempre, pero el modelo solo
    # miraba el estado (COT-007): un precio historico quedaba convertible
    # indefinidamente, aunque el documento entregado al cliente afirmara lo
    # contrario. Si el papel promete una vigencia, el backend tiene que
    # sostenerla.
    DIAS_VALIDEZ = 15

    @property
    def fecha_vencimiento(self):
        from datetime import timedelta

        return self.fecha_creacion + timedelta(days=self.DIAS_VALIDEZ)

    @property
    def esta_vencida(self):
        from django.utils import timezone

        if self.fecha_creacion is None:
            return False
        return timezone.now() > self.fecha_vencimiento


class DetalleCotizacion(models.Model):
    """
    Lineas de detalle de cotizacion.
    Misma estructura que DetalleVenta para facilitar conversion.
    """
    cotizacion = models.ForeignKey(
        Cotizacion,
        on_delete=models.CASCADE,
        related_name='detalles',
        verbose_name='Cotizacion'
    )

    producto = models.ForeignKey(
        'productos.Producto',
        on_delete=models.PROTECT,
        related_name='detalles_cotizacion',
        verbose_name='Producto'
    )

    cantidad = models.IntegerField(
        validators=[MinValueValidator(1)],
        verbose_name='Cantidad'
    )

    precio_unitario = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name='Precio Unitario'
    )

    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name='Subtotal'
    )

    descuento_monto = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Descuento (Monto)'
    )

    descuento_porcentaje = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Descuento (%)'
    )

    total_linea = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name='Total Linea'
    )

    class Meta:
        verbose_name = 'Detalle de Cotizacion'
        verbose_name_plural = 'Detalles de Cotizacion'
        # COT-008: la cotizacion es fuente autorizada de precio para la venta;
        # una linea con cantidad/precio/descuento imposible no debe persistir.
        constraints = [
            models.CheckConstraint(
                condition=models.Q(cantidad__gte=1),
                name='detallecotizacion_cantidad_positiva',
            ),
            models.CheckConstraint(
                condition=models.Q(precio_unitario__gte=Decimal('0.01')),
                name='detallecotizacion_precio_positivo',
            ),
            models.CheckConstraint(
                condition=models.Q(descuento_monto__gte=Decimal('0.00')),
                name='detallecotizacion_descuento_no_negativo',
            ),
            models.CheckConstraint(
                condition=models.Q(descuento_monto__lte=models.F('subtotal')),
                name='detallecotizacion_descuento_no_supera_subtotal',
            ),
            models.CheckConstraint(
                condition=models.Q(total_linea__gte=Decimal('0.00')),
                name='detallecotizacion_total_no_negativo',
            ),
        ]

    def __str__(self):
        return f"{self.producto.nombre} x {self.cantidad}"

    def save(self, *args, **kwargs):
        self.subtotal = self.cantidad * self.precio_unitario
        if self.subtotal > 0:
            self.descuento_porcentaje = (self.descuento_monto / self.subtotal) * 100
        self.total_linea = self.subtotal - self.descuento_monto
        super().save(*args, **kwargs)
