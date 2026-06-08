from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from decimal import Decimal
from django.conf import settings


class CierreCaja(models.Model):
    """
    Registro de cierre de caja diario
    Generado automáticamente a las 10 PM
    """
    # Fecha del cierre
    fecha = models.DateField(
        unique=True,
        help_text="Fecha del día que se está cerrando"
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
    
    # Control
    cerrado = models.BooleanField(
        default=True,
        help_text="Indica si el cierre está finalizado"
    )
    
    class Meta:
        db_table = 'cierres_caja'
        ordering = ['-fecha']
        verbose_name = 'Cierre de Caja'
        verbose_name_plural = 'Cierres de Caja'
    
    def __str__(self):
        return f"Cierre {self.fecha} - ${self.total_ventas}"
    

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
        max_digits=5,
        decimal_places=2,
        help_text="Margen de ganancia promedio (%)"
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
    
    def __str__(self):
        return f"{self.producto.nombre} - {self.cantidad_vendida} unidades"


class InventarioValorizado(models.Model):
    """
    Snapshot de inventario valorizado en un momento específico
    """
    # Fecha del snapshot
    fecha = models.DateField(
        help_text="Fecha del inventario"
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
    
    def __str__(self):
        return f"Inventario {self.fecha} - ${self.valor_total_inventario}"
