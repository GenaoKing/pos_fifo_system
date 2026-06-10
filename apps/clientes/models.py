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

    class Meta:
        verbose_name = 'Cliente'
        verbose_name_plural = 'Clientes'
        ordering = ['nombre']
        db_table = 'clientes'
        indexes = [
            models.Index(fields=['cedula_rnc']),
            models.Index(fields=['tipo', 'activo']),
            models.Index(fields=['nombre']),
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
