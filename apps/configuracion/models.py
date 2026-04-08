"""
apps/configuracion/models.py
Configuracion centralizada del negocio + feature flags
Singleton pattern: siempre pk=1
"""
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


class ConfiguracionNegocio(models.Model):
    """
    Configuracion unica por instalacion.
    Controla feature flags, datos del negocio, y parametros operativos.
    Se consulta en caliente via get_config() cacheado.
    """

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
        'Escaner de Codigo de Barras',
        default=True,
        help_text='Soporte para escaner de codigo de barras en POS'
    )
    modulo_reportes_ondemand = models.BooleanField(
        'Reportes On-Demand',
        default=True,
        help_text='Generacion manual de reportes con filtros'
    )
    modulo_ecf = models.BooleanField(
        'Facturacion Electronica (e-CF)',
        default=False,
        help_text='Integracion con DGII para comprobantes fiscales electronicos'
    )
    modulo_dashboard = models.BooleanField(
        'Dashboard',
        default=True,
        help_text='Panel de metricas y KPIs'
    )

    # =========================================================================
    # METODOS DE PAGO HABILITADOS
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
    hora_cierre_automatico = models.TimeField(
        'Hora de cierre automatico',
        default='19:00',
        help_text='Hora a la que se ejecuta el cierre de caja diario'
    )
    dias_limite_anulacion = models.PositiveIntegerField(
        'Dias limite para anulacion',
        default=15,
        validators=[MinValueValidator(1), MaxValueValidator(365)],
        help_text='Cantidad de dias despues de los cuales no se puede anular una venta'
    )
    stock_minimo_default = models.PositiveIntegerField(
        'Stock minimo por defecto',
        default=5,
        help_text='Stock minimo predeterminado al crear productos nuevos'
    )
    prefijo_numero_venta = models.CharField(
        'Prefijo numero de venta',
        max_length=10,
        default='VTA',
        help_text='Prefijo para numeros de venta (ej: VTA-20260406-00001)'
    )
    formato_codigo_barras = models.CharField(
        'Formato codigo de barras interno',
        max_length=20,
        default='RP-XXXXXX',
        help_text='Formato para codigos de barras generados internamente'
    )
    permitir_inventario_negativo = models.BooleanField(
        'Permitir inventario negativo',
        default=True,
        help_text='Permitir ventas cuando el stock es insuficiente (se registra en auditoria)'
    )

    # =========================================================================
    # IMPRESION
    # =========================================================================
    nombre_impresora_termica = models.CharField(
        'Nombre impresora termica',
        max_length=100,
        blank=True,
        help_text='Nombre exacto como aparece en Windows (ej: 2C-POS80-01)'
    )
    nombre_impresora_zebra = models.CharField(
        'Nombre impresora Zebra',
        max_length=100,
        blank=True,
        help_text='Nombre exacto como aparece en Windows (ej: ZDesigner LP 2824)'
    )
    texto_pie_ticket = models.TextField(
        'Texto pie de ticket',
        blank=True,
        default='Gracias por su compra',
        help_text='Texto que aparece al final del ticket de venta'
    )
    imprimir_logo_ticket = models.BooleanField(
        'Imprimir logo en ticket',
        default=True
    )

    # =========================================================================
    # METADATA
    # =========================================================================
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuracion del Negocio'
        verbose_name_plural = 'Configuracion del Negocio'

    def __str__(self):
        return f'Configuracion: {self.nombre_negocio}'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        # Invalidar cache al guardar
        from django.core.cache import cache
        cache.delete('config_negocio')

    def delete(self, *args, **kwargs):
        pass  # No permitir eliminar el singleton

    @classmethod
    def load(cls):
        """Carga o crea la configuracion singleton"""
        obj, _ = cls.objects.get_or_create(pk=1)
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