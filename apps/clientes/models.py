from django.db import models
from django.core.validators import MaxValueValidator, MinValueValidator
from django.conf import settings
from decimal import Decimal
from django.utils import timezone


class Cliente(models.Model):
    """
    Clientes del sistema.
    Incluye cliente generico CONTADO para ventas rapidas.
    """
    TIPOS = [
        ('CONTADO', 'Contado'),
        ('PERSONAL', 'Personal'),
        ('CORPORATIVO', 'Corporativo'),
    ]

    tipo = models.CharField(
        max_length=20,
        choices=TIPOS,
        default='PERSONAL',
        verbose_name='Tipo de Cliente'
    )

    # Identificacion
    nombre = models.CharField(
        max_length=200,
        verbose_name='Nombre / Razon Social'
    )

    cedula_rnc = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        unique=True,
        verbose_name='Cedula / RNC',
        help_text='Cedula para personas, RNC para empresas'
    )

    # Contacto
    telefono = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        verbose_name='Telefono'
    )

    direccion = models.TextField(
        blank=True,
        null=True,
        verbose_name='Direccion'
    )

    # Condiciones comerciales (corporativo)
    limite_credito = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Limite de Credito',
        help_text='0 = sin credito'
    )

    plazo_credito_dias = models.PositiveIntegerField(
        default=30,
        validators=[MinValueValidator(1), MaxValueValidator(365)],
        verbose_name='Plazo de Credito (dias)',
        help_text='Dias de vencimiento para ventas a credito con vencimiento unico'
    )

    condiciones_pago = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='Condiciones de Pago',
        help_text='Ej: 30 dias, contado, etc.'
    )

    notas = models.TextField(
        blank=True,
        null=True,
        verbose_name='Notas'
    )

    activo = models.BooleanField(
        default=True,
        verbose_name='Activo'
    )

    fecha_creacion = models.DateTimeField(
        default=timezone.now,
        verbose_name='Fecha de Creacion'
    )

    fecha_modificacion = models.DateTimeField(
        auto_now=True,
        verbose_name='Fecha de Modificacion'
    )

    # ------------------------------------------------------------------
    # Origen (solo se usa en el CLOUD; en el POS local quedan en NULL)
    # ------------------------------------------------------------------
    #
    # `cedula_rnc` es opcional en el negocio real: la mayoria de los clientes
    # de mostrador no la dan. Usarla como unica clave natural hacia que el
    # cloud no pudiera identificar al cliente de una venta o de una cuenta por
    # cobrar, y las CxC se rechazaban para siempre (BUG-C en docs/BUGS.md).
    #
    # Estos dos campos dan una identidad estable que no depende de datos que
    # el negocio puede omitir: de que sucursal vino y con que PK nacio alli.
    origen_sucursal = models.ForeignKey(
        'sucursales.Sucursal',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='clientes_originados',
        verbose_name='Sucursal de origen',
        help_text='Sucursal donde se creo el cliente, si nacio en una sucursal.'
    )
    origen_id_local = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='ID local de origen',
        help_text='PK que tiene el cliente en la BD de su sucursal de origen.'
    )

    # Identidad cloud estable. Ver apps/sync/engine.py::_pull_clientes.
    #
    # El pull identificaba por clave natural (cedula_rnc, o nombre+tipo cuando
    # no hay cedula), asi que renombrar un cliente -- o corregirle la cedula --
    # creaba OTRO en la sucursal y dejaba sus ventas y su cartera colgando del
    # viejo. Con este campo la fila se reconoce aunque cambie cualquier
    # atributo visible.
    #
    # Null = fila de origen local todavia no reconciliada con el cloud. La
    # clave natural sigue sirviendo para adoptarla la primera vez.
    origen_cloud_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        verbose_name='ID en cloud',
        help_text='PK de esta fila en la BD cloud. Identidad de sync; no se edita a mano.',
    )

    class Meta:
        verbose_name = 'Cliente'
        verbose_name_plural = 'Clientes'
        ordering = ['nombre']
        db_table = 'clientes'
        indexes = [
            models.Index(fields=['cedula_rnc']),
            models.Index(fields=['tipo', 'activo']),
            models.Index(fields=['nombre']),
            models.Index(fields=['origen_sucursal', 'origen_id_local']),
            # Cursor de sync: se filtra y ordena por este par en cada pull.
            models.Index(fields=['fecha_modificacion', 'id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['origen_sucursal', 'origen_id_local'],
                condition=models.Q(
                    origen_sucursal__isnull=False,
                    origen_id_local__isnull=False,
                ),
                name='cliente_origen_unico_por_sucursal',
            ),
        ]

    def __str__(self):
        if self.cedula_rnc:
            return f"{self.nombre} ({self.cedula_rnc})"
        return self.nombre

    @property
    def es_contado(self):
        return self.tipo == 'CONTADO'

    @property
    def total_compras(self):
        return self.ventas.filter(estado='COMPLETADA').count()

    @property
    def monto_total_compras(self):
        from django.db.models import Sum
        total = self.ventas.filter(
            estado='COMPLETADA'
        ).aggregate(total=Sum('total'))['total']
        return total or Decimal('0.00')

    @classmethod
    def get_cliente_contado(cls):
        """Obtiene o crea el cliente generico CONTADO"""
        cliente, created = cls.objects.get_or_create(
            tipo='CONTADO',
            nombre='CLIENTE CONTADO',
            defaults={
                'activo': True,
                'notas': 'Cliente generico para ventas de contado'
            }
        )
        return cliente
