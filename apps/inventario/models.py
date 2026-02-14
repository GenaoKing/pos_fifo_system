from django.db import models
from django.core.validators import MinValueValidator
from decimal import Decimal
from datetime import datetime
from django.conf import settings


class Compra(models.Model):
    """
    Registro de compras a proveedores
    """
    numero_compra = models.CharField(
        max_length=50,
        unique=True,
        verbose_name='Número de Compra'
    )
    
    proveedor = models.CharField(
        max_length=200,
        verbose_name='Proveedor'
    )
    
    fecha_compra = models.DateTimeField(
        verbose_name='Fecha de Compra',
        auto_now_add=True
    )

    numero_factura = models.CharField(
        max_length=50,
        blank=True, 
        null=True)
    
    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name='Total'
    )
    
    notas = models.TextField(
        blank=True,
        null=True,
        verbose_name='Notas'
    )
    
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='compras',
        verbose_name='Registrado por'
    )
    
    fecha_creacion = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de Registro'
    )
    
    class Meta:
        verbose_name = 'Compra'
        verbose_name_plural = 'Compras'
        ordering = ['-fecha_compra']
        indexes = [
            models.Index(fields=['numero_compra']),
            models.Index(fields=['fecha_compra']),
        ]
    
    def __str__(self):
        return f"Compra {self.numero_compra} - {self.proveedor}"
    
    def save(self, *args, **kwargs):
        if not self.numero_compra:
            # Auto-generar número de compra: COMP-20260201-00001
            fecha = datetime.now().strftime('%Y%m%d')
            ultimo = Compra.objects.filter(
                numero_compra__startswith=f'COMP-{fecha}'
            ).count()
            self.numero_compra = f'COMP-{fecha}-{str(ultimo + 1).zfill(5)}'
        super().save(*args, **kwargs)


class DetalleCompra(models.Model):
    """
    Detalle de productos en una compra
    ⭐ Al guardar, auto-genera un Lote
    """
    compra = models.ForeignKey(
        Compra,
        on_delete=models.CASCADE,
        related_name='detalles',
        verbose_name='Compra'
    )
    
    producto = models.ForeignKey(
        'productos.Producto',
        on_delete=models.PROTECT,
        related_name='detalles_compra',
        verbose_name='Producto'
    )
    
    cantidad = models.IntegerField(
        validators=[MinValueValidator(1)],
        verbose_name='Cantidad'
    )
    
    costo_unitario = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name='Costo Unitario'
    )
    
    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name='Subtotal'
    )
    
    class Meta:
        verbose_name = 'Detalle de Compra'
        verbose_name_plural = 'Detalles de Compra'
    
    def __str__(self):
        return f"{self.producto.nombre} - {self.cantidad} unidades"
    
    def save(self, *args, **kwargs):
        # Calcular subtotal
        self.subtotal = self.cantidad * self.costo_unitario
        super().save(*args, **kwargs)
        
        # Auto-crear lote después de guardar
        if not hasattr(self, '_lote_creado'):
            self._crear_lote()
    
    def _crear_lote(self):
        """
        Crea automáticamente un lote para este detalle
        ⭐ 1 DetalleCompra = 1 Lote + 1 MovimientoLote
        """
        # Generar número de lote: LOTE-20260201-00001
        fecha = self.compra.fecha_compra.strftime('%Y%m%d')
        ultimo = Lote.objects.filter(
            numero_lote__startswith=f'LOTE-{fecha}'
        ).count()
        numero_lote = f'LOTE-{fecha}-{str(ultimo + 1).zfill(5)}'
        
        # Crear el lote
        lote = Lote.objects.create(
            producto=self.producto,
            detalle_compra=self,
            numero_lote=numero_lote,
            fecha_compra=self.compra.fecha_compra,
            cantidad_inicial=self.cantidad,
            cantidad_actual=self.cantidad,
            costo_unitario=self.costo_unitario
        )
        
        # Crear movimiento inicial
        MovimientoLote.objects.create(
            lote=lote,
            tipo='COMPRA',
            cantidad=self.cantidad,
            cantidad_anterior=0,
            cantidad_nueva=self.cantidad,
            referencia_tipo='Compra',
            referencia_id=self.compra.id,
            usuario=self.compra.usuario,
            notas=f'Compra inicial - {self.compra.numero_compra}'
        )
        
        self._lote_creado = True






class Lote(models.Model):
    """
    Core del sistema FIFO - cada compra genera un lote
    """
    producto = models.ForeignKey(
        'productos.Producto',
        on_delete=models.PROTECT,
        related_name='lotes',
        verbose_name='Producto'
    )
    

    detalle_compra = models.OneToOneField(
        'DetalleCompra',
        on_delete=models.PROTECT,
        related_name='lote',
        verbose_name='Detalle de Compra',
        help_text='Línea de compra que generó este lote',
    )

    numero_lote = models.CharField(
        max_length=50,
        unique=True,
        verbose_name='Número de Lote'
    )
    
    fecha_compra = models.DateTimeField(
        verbose_name='Fecha de Compra',
        db_index=True
    )
    
    cantidad_inicial = models.IntegerField(
        validators=[MinValueValidator(1)],
        verbose_name='Cantidad Inicial'
    )
    
    cantidad_actual = models.IntegerField(
        verbose_name='Cantidad Actual'
    )
    
    costo_unitario = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name='Costo Unitario'
    )
    
    activo = models.BooleanField(
        default=True,
        verbose_name='Activo'
    )
    
    fecha_creacion = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de Creación'
    )
    
    class Meta:
        verbose_name = 'Lote'
        verbose_name_plural = 'Lotes'
        ordering = ['fecha_compra', 'id']  # FIFO: más antiguo primero
        indexes = [
            models.Index(fields=['producto', 'fecha_compra']),
            models.Index(fields=['cantidad_actual']),
        ]
    
    def __str__(self):
        return f"{self.numero_lote} - {self.producto.nombre} ({self.cantidad_actual}/{self.cantidad_inicial})"
    
    def esta_agotado(self):
        return self.cantidad_actual <= 0
    
    def get_valor_actual(self):
        return self.cantidad_actual * self.costo_unitario


class MovimientoLote(models.Model):
    """
    Historial completo de movimientos de cada lote
    Cada cambio en cantidad_actual genera un registro aquí
    """
    TIPOS_MOVIMIENTO = [
        ('COMPRA', 'Compra Inicial'),
        ('VENTA', 'Venta'),
        ('AJUSTE', 'Ajuste Manual'),
        ('ANULACION', 'Anulación de Venta'),
        ('MERMA', 'Merma'),
        ('DANO', 'Daño'),
    ]
    
    lote = models.ForeignKey(
        'Lote',
        on_delete=models.CASCADE,
        related_name='movimientos',
        verbose_name='Lote'
    )
    
    tipo = models.CharField(
        max_length=20,
        choices=TIPOS_MOVIMIENTO,
        verbose_name='Tipo de Movimiento'
    )
    
    cantidad = models.IntegerField(
        verbose_name='Cantidad',
        help_text='Positivo = entrada, Negativo = salida'
    )
    
    cantidad_anterior = models.IntegerField(
        verbose_name='Cantidad Anterior'
    )
    
    cantidad_nueva = models.IntegerField(
        verbose_name='Cantidad Nueva'
    )
    
    referencia_tipo = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name='Tipo de Referencia',
        help_text='Ej: Venta, Ajuste, etc.'
    )
    
    referencia_id = models.IntegerField(
        blank=True,
        null=True,
        verbose_name='ID de Referencia'
    )
    
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='movimientos_lote',
        verbose_name='Usuario'
    )
    
    notas = models.TextField(
        blank=True,
        null=True,
        verbose_name='Notas'
    )
    
    fecha_creacion = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha del Movimiento'
    )
    
    class Meta:
        verbose_name = 'Movimiento de Lote'
        verbose_name_plural = 'Movimientos de Lote'
        ordering = ['-fecha_creacion']
        indexes = [
            models.Index(fields=['lote', 'tipo']),
            models.Index(fields=['fecha_creacion']),
        ]
    
    def __str__(self):
        signo = '+' if self.cantidad > 0 else ''
        return f"{self.lote.numero_lote} - {self.get_tipo_display()} ({signo}{self.cantidad})"


class AjusteInventario(models.Model):
    """
    Ajustes manuales de inventario
    Merma, daño, conteo físico, correcciones
    """
    TIPOS_AJUSTE = [
        ('MERMA', 'Merma'),
        ('DANO', 'Daño'),
        ('CONTEO', 'Ajuste por Conteo'),
        ('CORRECCION', 'Corrección'),
        ('DEVOLUCION', 'Devolución'),
    ]
    
    lote = models.ForeignKey(
        'Lote',
        on_delete=models.PROTECT,
        related_name='ajustes',
        verbose_name='Lote'
    )
    
    tipo = models.CharField(
        max_length=20,
        choices=TIPOS_AJUSTE,
        verbose_name='Tipo de Ajuste'
    )
    
    cantidad = models.IntegerField(
        verbose_name='Cantidad',
        help_text='Positivo = agregar, Negativo = quitar'
    )
    
    motivo = models.TextField(
        verbose_name='Motivo'
    )
    
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='ajustes_inventario',
        verbose_name='Realizado por'
    )
    
    fecha_ajuste = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha del Ajuste'
    )
    
    class Meta:
        verbose_name = 'Ajuste de Inventario'
        verbose_name_plural = 'Ajustes de Inventario'
        ordering = ['-fecha_ajuste']
    
    def __str__(self):
        return f"{self.get_tipo_display()} - {self.lote.numero_lote} ({self.cantidad})"
    
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        
        # Guardar cantidad anterior
        cantidad_anterior = self.lote.cantidad_actual
        
        # Actualizar el lote
        self.lote.cantidad_actual += self.cantidad
        self.lote.save()
        
        # Crear movimiento
        MovimientoLote.objects.create(
            lote=self.lote,
            tipo='AJUSTE',
            cantidad=self.cantidad,
            cantidad_anterior=cantidad_anterior,
            cantidad_nueva=self.lote.cantidad_actual,
            referencia_tipo='AjusteInventario',
            referencia_id=self.id,
            usuario=self.usuario,
            notas=self.motivo
        )