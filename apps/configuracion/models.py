"""
apps/configuracion/models.py
Configuracion centralizada del negocio + feature flags

FASE 2: Ya no es singleton (pk=1).
Ahora es una config POR SUCURSAL via FK.
Backward compatible: si no hay sucursal configurada, carga la primera config existente.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


class ConfiguracionNegocio(models.Model):
    """
    Configuracion por sucursal.
    Cada sucursal tiene su propia config con feature flags y datos de negocio.
    Se consulta en caliente via get_config() cacheado.
    """

    # =========================================================================
    # SUCURSAL (Fase 2)
    # =========================================================================
    sucursal = models.OneToOneField(
        'sucursales.Sucursal',
        on_delete=models.PROTECT,
        related_name='configuracion',
        verbose_name='Sucursal',
        blank=True,
        null=True,
        help_text='Sucursal a la que pertenece esta configuracion. '
                  'Null para instalaciones legacy sin sucursal.'
    )

    # =========================================================================
    # IDENTIDAD DEL NEGOCIO
    # =========================================================================
    nombre_negocio = models.CharField(
        'Nombre del negocio',
        max_length=200,
        default='Mi Negocio'
    )
    rnc = models.CharField(
        'RNC',
        max_length=20,
        blank=True,
        help_text='Registro Nacional del Contribuyente'
    )
    direccion = models.TextField(
        'Direccion',
        blank=True
    )
    telefono = models.CharField(
        'Telefono',
        max_length=50,
        blank=True
    )
    email_negocio = models.EmailField(
        'Email del negocio',
        blank=True
    )
    logo = models.ImageField(
        'Logo',
        upload_to='config/',
        blank=True,
        null=True,
        help_text='Logo para tickets, PDFs y cotizaciones'
    )


    permitir_inventario_negativo = models.BooleanField(
        'Permitir inventario negativo',
        default=False,
        help_text='Si True, el sistema no bloquea ventas aunque el stock sea insuficiente. '
                  'Usar con precaucion, puede llevar a inconsistencias si no se controla bien'
                    'el flujo de compras e ingresos de mercancia.'
    )

    # =========================================================================
    # FEATURE FLAGS - MODULOS
    # =========================================================================
    modulo_etiquetas_zebra = models.BooleanField(
        'Etiquetas Zebra',
        default=False,
        help_text='Impresion de etiquetas con impresora Zebra LP 2824'
    )
    modulo_financiacion_coop = models.BooleanField(
        'Financiacion Cooperativa',
        default=False,
        help_text='Modulo de ventas con financiacion cooperativa y PDF formal'
    )
    modulo_cotizaciones = models.BooleanField(
        'Cotizaciones',
        default=True,
        help_text='Crear cotizaciones y convertir a ventas'
    )
    modulo_impresion_termica = models.BooleanField(
        'Impresion Termica',
        default=True,
        help_text='Impresion de tickets en impresora termica 80mm'
    )
    modulo_barcode_scanner = models.BooleanField(
        'Scanner de Codigo de Barras',
        default=True,
        help_text='Lector de codigos de barras en el POS'
    )
    modulo_reportes_ondemand = models.BooleanField(
        'Reportes On-Demand',
        default=True,
        help_text='Generacion de reportes personalizados'
    )
    modulo_ecf = models.BooleanField(
        'Facturacion Electronica (e-CF)',
        default=False,
        help_text='Emision de comprobantes fiscales electronicos (Ley 32-23)'
    )
    modulo_dashboard = models.BooleanField(
        'Dashboard',
        default=True,
        help_text='Panel de control con metricas en tiempo real'
    )

    # =========================================================================
    # METODOS DE PAGO
    # =========================================================================
    pago_efectivo = models.BooleanField(
        'Pago en Efectivo',
        default=True
    )
    pago_transferencia = models.BooleanField(
        'Pago por Transferencia',
        default=True
    )
    pago_tarjeta = models.BooleanField(
        'Pago con Tarjeta',
        default=False
    )

    # =========================================================================
    # PARAMETROS OPERATIVOS
    # =========================================================================
    formato_codigo_barras = models.CharField(
        'Formato Codigo de Barras',
        max_length=20,
        default='RP-XXXXXX',
        help_text='Formato para generacion de codigos internos'
    )
    dias_anulacion = models.PositiveIntegerField(
        'Dias para Anulacion',
        default=15,
        validators=[MinValueValidator(1), MaxValueValidator(365)],
        help_text='Cantidad de dias permitidos para anular una venta'
    )


     # =========================================================================
    # FACTURACION ELECTRONICA (e-CF)
    # =========================================================================
    # El feature flag `modulo_ecf` ya existe arriba en MODULOS.
    # Estos campos definen QUE proveedor usar y CON QUE entidad fiscal
    # se firman los documentos cuando el modulo esta activo.
    ecf_proveedor = models.CharField(
        'Proveedor e-CF',
        max_length=20,
        choices=(
            ('mseller', 'MSeller (PSFE)'),
            ('nativo', 'Libreria nativa (dgii-ecf-py)'),
        ),
        default='mseller',
        help_text='Implementacion a usar cuando modulo_ecf esta activo. '
                  'En Fase Inicial solo "mseller" esta disponible.'
    )
    emisor_activo = models.ForeignKey(
        'facturacion_electronica.Emisor',
        on_delete=models.PROTECT,
        related_name='configuraciones',
        verbose_name='Emisor activo',
        blank=True,
        null=True,
        help_text='Entidad fiscal que firma los e-CF de esta sucursal. '
                  'Requerido si modulo_ecf=True.'
    )
    itbis_incluido_en_precio = models.BooleanField(
        'ITBIS incluido en precio de venta',
        default=True,
        help_text='Si True, Producto.precio_venta ya incluye ITBIS y se '
                  'desglosa al emitir e-CF (back-calculo: base = precio/(1+pct)). '
                  'Si False, precio_venta es base imponible y el ITBIS se suma encima.'
    )
    itbis_porcentaje_global = models.DecimalField(
        'ITBIS % global',
        max_digits=5,
        decimal_places=2,
        default=Decimal('18.00'),
        help_text='Porcentaje de ITBIS aplicado por defecto a todos los productos. '
                  'Se usa hasta que Producto tenga su propio campo itbis_pct '
                  'para tasas diferenciadas (16%, exento, etc.).'
    )

    modo_contingencia = models.BooleanField(
        'Modo contingencia (Fase 2)',
        default=False,
        help_text='PLACEHOLDER FASE 2 — actualmente NO afecta el comportamiento. '
                  'Cuando se implemente: si True, el POS emite NCF papel '
                  '(serie B) en lugar de e-CF, durante el periodo en que '
                  'DGII está caída más de 24h. Requiere notificación previa '
                  'a DGII vía Oficina Virtual y plazo máximo 15 días. Al '
                  'volver a False, se dispara comando que convierte los '
                  'NCF papel del periodo en e-CF para enviar a DGII en los '
                  'siguientes 30 días.'
    )



    # =========================================================================
    # METADATA
    # =========================================================================
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuracion del Negocio'
        verbose_name_plural = 'Configuraciones del Negocio'

    def __str__(self):
        if self.sucursal:
            return f'Configuracion: {self.nombre_negocio} ({self.sucursal.codigo})'
        return f'Configuracion: {self.nombre_negocio}'

    def save(self, *args, **kwargs):
        # -----------------------------------------------------------
        # FASE 2: Ya NO forzamos self.pk = 1
        # Cada sucursal tiene su propia config.
        # -----------------------------------------------------------
        super().save(*args, **kwargs)
        # Invalidar cache al guardar
        from django.core.cache import cache
        if self.sucursal:
            cache.delete(f'config_negocio_{self.sucursal.codigo}')
            # Tambien invalidar cache de sucursal_actual por si cambio
            cache.delete(f'sucursal_actual_{self.sucursal.codigo}')
        else:
            # Fallback legacy: invalidar cache sin sucursal
            cache.delete('config_negocio')

    def delete(self, *args, **kwargs):
        pass  # No permitir eliminar configuracion

    @classmethod
    def load(cls, sucursal=None):
        """
        Carga la configuracion para una sucursal.

        Args:
            sucursal: instancia de Sucursal, o None para legacy/fallback

        Si se pasa sucursal, busca por FK.
        Si no hay sucursal, intenta cargar pk=1 (backward compatible).
        """
        if sucursal:
            obj, _ = cls.objects.get_or_create(
                sucursal=sucursal,
                defaults={'nombre_negocio': sucursal.nombre}
            )
            return obj
        else:
            # Backward compatible: cargar la primera config o crear una con pk=1
            obj = cls.objects.first()
            if obj is None:
                obj = cls.objects.create(pk=1)
            return obj

    def get_metodos_pago_activos(self):
        """Retorna lista de metodos de pago habilitados"""
        metodos = []
        if self.pago_efectivo:
            metodos.append('EFECTIVO')
        if self.pago_transferencia:
            metodos.append('TRANSFERENCIA')
        if self.pago_tarjeta:
            metodos.append('TARJETA')
        return metodos

    def get_modulos_activos(self):
        """Retorna dict de modulos y su estado"""
        return {
            'etiquetas_zebra': self.modulo_etiquetas_zebra,
            'financiacion_coop': self.modulo_financiacion_coop,
            'cotizaciones': self.modulo_cotizaciones,
            'impresion_termica': self.modulo_impresion_termica,
            'barcode_scanner': self.modulo_barcode_scanner,
            'reportes_ondemand': self.modulo_reportes_ondemand,
            'ecf': self.modulo_ecf,
            'dashboard': self.modulo_dashboard,
        }


class AccesoRapidoPOS(models.Model):
    """Boton configurable para acelerar la seleccion en el POS."""

    TIPO_PRODUCTO = 'producto'
    TIPO_CATEGORIA = 'categoria'
    TIPO_CHOICES = (
        (TIPO_PRODUCTO, 'Producto'),
        (TIPO_CATEGORIA, 'Categoria'),
    )

    COLOR_CHOICES = (
        ('azul', 'Azul'),
        ('verde', 'Verde'),
        ('ambar', 'Ambar'),
        ('gris', 'Gris'),
    )

    etiqueta = models.CharField(
        'Etiqueta',
        max_length=80,
        blank=True,
        help_text='Texto visible en el boton. Si se deja vacio, se usa el nombre del producto o categoria.',
    )
    tipo = models.CharField(
        'Tipo',
        max_length=20,
        choices=TIPO_CHOICES,
        default=TIPO_PRODUCTO,
    )
    producto = models.ForeignKey(
        'productos.Producto',
        on_delete=models.PROTECT,
        related_name='accesos_rapidos_pos',
        blank=True,
        null=True,
        help_text='Requerido cuando el tipo es Producto.',
    )
    categoria = models.ForeignKey(
        'productos.Categoria',
        on_delete=models.PROTECT,
        related_name='accesos_rapidos_pos',
        blank=True,
        null=True,
        help_text='Requerida cuando el tipo es Categoria.',
    )
    color = models.CharField(
        'Color',
        max_length=20,
        choices=COLOR_CHOICES,
        default='azul',
    )
    orden = models.PositiveIntegerField(
        'Orden',
        default=0,
        help_text='Menor numero aparece primero.',
    )
    activo = models.BooleanField('Activo', default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Acceso rapido POS'
        verbose_name_plural = 'Accesos rapidos POS'
        ordering = ['orden', 'id']

    def __str__(self):
        return self.etiqueta_visible

    @property
    def etiqueta_visible(self):
        if self.etiqueta:
            return self.etiqueta
        if self.tipo == self.TIPO_PRODUCTO and self.producto_id:
            return self.producto.nombre
        if self.tipo == self.TIPO_CATEGORIA and self.categoria_id:
            return self.categoria.nombre
        return 'Acceso rapido POS'

    def clean(self):
        errors = {}
        if self.tipo == self.TIPO_PRODUCTO:
            if not self.producto_id:
                errors['producto'] = 'Seleccione un producto para este acceso rapido.'
            if self.categoria_id:
                errors['categoria'] = 'Un acceso de producto no debe tener categoria.'
        elif self.tipo == self.TIPO_CATEGORIA:
            if not self.categoria_id:
                errors['categoria'] = 'Seleccione una categoria para este acceso rapido.'
            if self.producto_id:
                errors['producto'] = 'Un acceso de categoria no debe tener producto.'

        if errors:
            raise ValidationError(errors)
