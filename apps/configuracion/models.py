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

from apps.tenancy.media import config_logo_upload_to


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
        upload_to=config_logo_upload_to,
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
    cantidad_copias_ticket = models.PositiveSmallIntegerField(
        'Cantidad de copias de ticket',
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text='Total de tickets a imprimir por venta. Use 2 para cliente + archivo interno.'
    )

    # =========================================================================
    # CONTROL DE DESCUENTOS
    # =========================================================================
    # Gate opcional. Con `descuento_requiere_autorizacion` activo, un descuento
    # que supere la tolerancia exige una autorizacion puntual emitida por
    # alguien con el permiso `ventas.autorizar_descuento` — tipicamente pasando
    # su carnet por el lector del POS.
    # Ver apps.permisos.models.AutorizacionOverride.OP_VENTA_DESCUENTO.
    #
    # Apagado por defecto: una instalacion existente se comporta igual que antes.
    MOTIVO_NINGUNO = 'NINGUNO'
    MOTIVO_OPCIONAL = 'OPCIONAL'
    MOTIVO_OBLIGATORIO = 'OBLIGATORIO'
    MOTIVO_DESCUENTO_CHOICES = (
        (MOTIVO_NINGUNO, 'No pedir motivo'),
        (MOTIVO_OPCIONAL, 'Pedir motivo, permitir dejarlo vacio'),
        (MOTIVO_OBLIGATORIO, 'Exigir motivo'),
    )

    descuento_requiere_autorizacion = models.BooleanField(
        'Descuentos requieren autorizacion',
        default=False,
        help_text='Si True, un descuento que supere la tolerancia no se puede '
                  'cerrar sin la autorizacion de un supervisor.'
    )
    descuento_tolerancia_monto = models.DecimalField(
        'Tolerancia de descuento (monto)',
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text='Descuento en pesos que el cajero puede aplicar sin pedir '
                  'autorizacion. 0 = ninguno.'
    )
    descuento_tolerancia_porcentaje = models.DecimalField(
        'Tolerancia de descuento (%)',
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00')), MaxValueValidator(Decimal('100.00'))],
        help_text='Descuento como % del subtotal que el cajero puede aplicar '
                  'sin pedir autorizacion. 0 = ninguno.'
    )
    descuento_motivo_modo = models.CharField(
        'Motivo del descuento',
        max_length=12,
        choices=MOTIVO_DESCUENTO_CHOICES,
        default=MOTIVO_NINGUNO,
        help_text='Cuanto se le exige al supervisor al autorizar. En un negocio '
                  'donde se regatea casi toda venta, pedir texto libre cada vez '
                  'termina en 400 filas que dicen "descuento": por eso el default '
                  'es no pedirlo.'
    )
    descuento_vigencia_minutos = models.PositiveSmallIntegerField(
        'Vigencia de la autorizacion de descuento (minutos)',
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(60)],
        help_text='Cuanto vive el token de autorizacion antes de vencer.'
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
            # Backward compatible: cargar la primera config o crear una con pk=1.
            # El leer-y-crear tiene carrera: en una instalacion recien montada,
            # dos requests simultaneos veian None los dos y el segundo moria con
            # IntegrityError sobre la pk. `get_or_create` reintenta la lectura
            # dentro de su propio savepoint.
            obj = cls.objects.first()
            if obj is None:
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

    # =========================================================================
    # CONTROL DE DESCUENTOS
    # =========================================================================
    def descuento_requiere_token(self, *, subtotal, descuento_total):
        """
        True si este descuento necesita la autorizacion de un supervisor.

        UNA sola implementacion de la regla, consumida por el POS (para decidir
        si abre el modal) y por ventas_service (para exigirla). Si viviera dos
        veces, la UI y el servidor terminarian discrepando.

        El descuento pasa libre si cae dentro de CUALQUIERA de las dos
        tolerancias. El `or` es deliberado:

            monto=100, pct=0   -> libre hasta RD$100 (margen solo en pesos)
            monto=0,   pct=5   -> libre hasta 5% (margen solo porcentual)
            monto=100, pct=5   -> libre si cumple una de las dos
            monto=0,   pct=0   -> cualquier descuento pide autorizacion

        Con un `and`, dejar una tolerancia en 0 anularia la otra.
        """
        if not self.descuento_requiere_autorizacion:
            return False

        descuento_total = Decimal(str(descuento_total or 0))
        if descuento_total <= 0:
            return False

        subtotal = Decimal(str(subtotal or 0))
        porcentaje = (
            (descuento_total / subtotal) * Decimal('100')
            if subtotal > 0 else Decimal('100')
        )

        dentro_del_margen = (
            descuento_total <= self.descuento_tolerancia_monto
            or porcentaje <= self.descuento_tolerancia_porcentaje
        )
        return not dentro_del_margen

    @property
    def descuento_motivo_obligatorio(self):
        return self.descuento_motivo_modo == self.MOTIVO_OBLIGATORIO

    @property
    def descuento_pide_motivo(self):
        return self.descuento_motivo_modo != self.MOTIVO_NINGUNO


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
