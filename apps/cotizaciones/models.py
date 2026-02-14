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
        unique=True,
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

    # Referencia a la venta si fue convertida
    venta = models.OneToOneField(
        'ventas.Venta',
        on_delete=models.SET_NULL,
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
        ]

    def __str__(self):
        return f"Cotizacion {self.numero_cotizacion} - ${self.total}"

    def save(self, *args, **kwargs):
        if not self.pk and not self.fecha_creacion:
            santo_domingo_tz = pytz.timezone('America/Santo_Domingo')
            self.fecha_creacion = timezone.now().astimezone(santo_domingo_tz)

        if not self.numero_cotizacion:
            fecha_str = self.fecha_creacion.strftime('%Y%m%d')
            ultimo = Cotizacion.objects.filter(
                numero_cotizacion__startswith=f'COT-{fecha_str}'
            ).count()
            self.numero_cotizacion = f'COT-{fecha_str}-{str(ultimo + 1).zfill(5)}'

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
        """Solo se puede convertir si esta pendiente"""
        return self.estado == 'PENDIENTE'


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

    def __str__(self):
        return f"{self.producto.nombre} x {self.cantidad}"

    def save(self, *args, **kwargs):
        self.subtotal = self.cantidad * self.precio_unitario
        if self.subtotal > 0:
            self.descuento_porcentaje = (self.descuento_monto / self.subtotal) * 100
        self.total_linea = self.subtotal - self.descuento_monto
        super().save(*args, **kwargs)