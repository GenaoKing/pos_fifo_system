from django.db import models
from django.utils import timezone
from django.contrib.postgres.indexes import GinIndex

from apps.tenancy.media import producto_image_upload_to


class Categoria(models.Model):
    """Categorías para agrupar productos"""
    
    nombre = models.CharField(
        'Nombre',
        max_length=100,
        unique=True,
    )
    descripcion = models.TextField(
        'Descripción',
        blank=True,
        help_text='Descripción opcional de la categoría',
    )
    activa = models.BooleanField(
        'Activa',
        default=True,
        help_text='Indica si la categoría está activa',
    )

    # ← AGREGAR ESTOS DOS CAMPOS NUEVOS:
    tipo_negocio = models.CharField(
        'Tipo de Negocio',
        max_length=50,
        choices=[
            ('general', 'General'),
            ('plasticos', 'Plásticos / Envases'),
            ('autopartes', 'Autopartes / Repuestos'),
            ('otro', 'Otro'),
        ],
        default='general',
        help_text='Tipo de industria/negocio de esta categoría'
    )
    
    atributos_configurados = models.JSONField(
        'Atributos Configurados',
        default=dict,
        blank=True,
        help_text='Definición de atributos personalizados para productos de esta categoría'
    )
    
    # Fechas
    fecha_creacion = models.DateTimeField('Fecha de creación', default=timezone.now)
    fecha_modificacion = models.DateTimeField('Fecha de modificación', auto_now=True)
    
    class Meta:
        verbose_name = 'Categoría'
        verbose_name_plural = 'Categorías'
        ordering = ['nombre']
        db_table = 'categorias'
        indexes = [
            # Cursor de sync: se filtra y ordena por este par en cada pull.
            models.Index(fields=['fecha_modificacion', 'id']),
        ]
    
    def __str__(self):
        return self.nombre
    
    @property
    def total_productos(self):
        """Retorna el total de productos activos en esta categoría"""
        return self.productos.filter(activo=True).count()


class Producto(models.Model):
    """Productos del inventario"""
    
    # Identificadores
    sku = models.CharField(
        'SKU',
        max_length=50,
        unique=True,
        blank=True,
        help_text='Código interno único del producto',
    )
    codigo_barras = models.CharField(
        'Código de barras',
        max_length=100,
        unique=True,
        blank=True,
        null=True,
        help_text='Código de barras externo o generado internamente (RP-XXXXXX)',
    )
    
    # Información básica
    nombre = models.CharField(
        'Nombre',
        max_length=200,
    )
    descripcion = models.TextField(
        'Descripción',
        blank=True,
    )
    
    # Categoría
    categoria = models.ForeignKey(
        Categoria,
        on_delete=models.PROTECT,
        related_name='productos',
        verbose_name='Categoría',
    )
    
    ESTADO_CHOICES = [
        ('nuevo', 'Nuevo'),
        ('usado', 'Usado'),
    ]

    estado = models.CharField(
        max_length=10, 
        choices=ESTADO_CHOICES,
        default='nuevo', 
        verbose_name='Estado'
        )
    
    marca = models.CharField(
        max_length=100,
        blank=True, 
        default='', 
        verbose_name='Marca'
        )

    # Precios y stock
    precio_venta = models.DecimalField(
        'Precio de venta',
        max_digits=10,
        decimal_places=2,
        help_text='Precio de venta al público',
    )
    stock_minimo = models.PositiveIntegerField(
        'Stock mínimo',
        default=5,
        help_text='Cantidad mínima antes de alertar',
    )
    
    # Estado
    activo = models.BooleanField(
        'Activo',
        default=True,
        help_text='Indica si el producto está disponible para venta',
    )
    
    # Imagen (opcional)
    imagen = models.ImageField(
        'Imagen',
        upload_to=producto_image_upload_to,
        blank=True,
        null=True,
    )

        # ← AGREGAR AQUÍ (línea ~90):
    atributos = models.JSONField(
        'Atributos Personalizados',
        default=dict,
        blank=True,
        help_text='Atributos específicos según el tipo de producto (marca, modelo, etc.)'
    )
    
    
    # Fechas
    fecha_creacion = models.DateTimeField('Fecha de creación', default=timezone.now)
    fecha_modificacion = models.DateTimeField('Fecha de modificación', auto_now=True)
    
    class Meta:
        verbose_name = 'Producto'
        verbose_name_plural = 'Productos'
        ordering = ['nombre']
        db_table = 'productos'
        indexes = [
            models.Index(fields=['sku']),
            models.Index(fields=['codigo_barras']),
            models.Index(fields=['categoria', 'activo']),
            # Cursor de sync: se filtra y ordena por este par en cada pull.
            models.Index(fields=['fecha_modificacion', 'id']),
            GinIndex(fields=['atributos'], name='idx_productos_atributos'),
        ]
    
    def __str__(self):
        return f"{self.sku} - {self.nombre}"
    
    @property
    def stock_actual(self):
        """Retorna el stock actual del producto sumando todos los lotes activos"""
        from apps.inventario.models import Lote
        return Lote.objects.filter(
            producto=self,
            activo=True
        ).aggregate(
            total=models.Sum('cantidad_actual')
        )['total'] or 0
    
    @property
    def necesita_reposicion(self):
        """Retorna True si el stock está por debajo del mínimo"""
        return self.stock_actual <= self.stock_minimo
    
    @property
    def valuacion_fifo(self):
        """Retorna la valuación FIFO del inventario actual del producto"""
        from apps.inventario.models import Lote
        lotes = Lote.objects.filter(
            producto=self,
            cantidad_actual__gt=0,
            activo=True
        )
        return sum(lote.cantidad_actual * lote.costo_unitario for lote in lotes)
    
    @property
    def es_codigo_interno(self):
        """Retorna True si el código de barras fue generado internamente"""
        return self.codigo_barras and self.codigo_barras.startswith('RP-')

    def imprimir_etiqueta(self, cantidad=1):
        """
        Imprime etiqueta(s) del producto usando impresora Zebra
        
        Args:
            cantidad: Número de etiquetas a imprimir
        
        Returns:
            dict con resultado de la impresión
        """
        from utils.impresoras.zebra import imprimir_etiqueta_producto
        return imprimir_etiqueta_producto(self, cantidad)


    @classmethod
    def generar_sku(cls):
        """Genera SKU secuencial: PROD-0001, PROD-0002, etc."""
        ultimo = cls.objects.order_by('-id').first()
        siguiente_num = (ultimo.id + 1) if ultimo else 1
        return f"PROD-{siguiente_num:04d}"
