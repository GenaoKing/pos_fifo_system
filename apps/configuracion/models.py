"""
apps/configuracion/models.py
Configuracion centralizada del negocio + feature flags

FASE 2: Ya no es singleton (pk=1).
Ahora es una config POR SUCURSAL via FK.
Backward compatible: si no hay sucursal configurada, carga la primera config existente.
"""
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