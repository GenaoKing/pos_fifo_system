from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class MetodoPlazoCredito(models.Model):
    TIPO_VENCIMIENTO_UNICO = 'VENCIMIENTO_UNICO'
    TIPO_CUOTAS = 'CUOTAS'
    TIPO_CHOICES = (
        (TIPO_VENCIMIENTO_UNICO, 'Vencimiento unico'),
        (TIPO_CUOTAS, 'Cuotas'),
    )

    FRECUENCIA_DIAS = 'DIAS'
    FRECUENCIA_SEMANAL = 'SEMANAL'
    FRECUENCIA_QUINCENAL = 'QUINCENAL'
    FRECUENCIA_MENSUAL = 'MENSUAL'
    FRECUENCIA_CHOICES = (
        (FRECUENCIA_DIAS, 'Cada N dias'),
        (FRECUENCIA_SEMANAL, 'Semanal'),
        (FRECUENCIA_QUINCENAL, 'Quincenal'),
        (FRECUENCIA_MENSUAL, 'Mensual'),
    )

    nombre = models.CharField(max_length=100, unique=True)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, default=TIPO_VENCIMIENTO_UNICO)
    dias_vencimiento = models.PositiveIntegerField(default=30, validators=[MinValueValidator(1)])
    cantidad_cuotas = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    frecuencia = models.CharField(max_length=20, choices=FRECUENCIA_CHOICES, default=FRECUENCIA_MENSUAL)
    inicial_minima_porcentaje = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00')), MaxValueValidator(Decimal('100.00'))],
    )
    interes_porcentaje = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00')), MaxValueValidator(Decimal('100.00'))],
        help_text='Interes de financiamiento por defecto; editable por venta en el POS.',
    )
    activo = models.BooleanField(default=True)
    sucursal = models.ForeignKey(
        'sucursales.Sucursal',
        on_delete=models.PROTECT,
        related_name='metodos_plazo_credito',
        blank=True,
        null=True,
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Metodo de plazo de credito'
        verbose_name_plural = 'Metodos de plazo de credito'
        ordering = ['nombre']
        indexes = [
            models.Index(fields=['activo', 'tipo']),
            models.Index(fields=['sucursal', 'activo']),
        ]

    def __str__(self):
        return self.nombre

    def normalizar_cantidad_cuotas(self, cantidad_solicitada=None) -> int:
        if self.tipo == self.TIPO_VENCIMIENTO_UNICO:
            return 1
        cantidad = cantidad_solicitada or self.cantidad_cuotas
        return max(int(cantidad), 1)

    def dias_entre_cuotas(self) -> int:
        if self.frecuencia == self.FRECUENCIA_SEMANAL:
            return 7
        if self.frecuencia == self.FRECUENCIA_QUINCENAL:
            return 15
        if self.frecuencia == self.FRECUENCIA_MENSUAL:
            return 30
        return max(int(self.dias_vencimiento), 1)


class CuentaPorCobrar(models.Model):
    ESTADO_ABIERTA = 'ABIERTA'
    ESTADO_PARCIAL = 'PARCIAL'
    ESTADO_PAGADA = 'PAGADA'
    ESTADO_VENCIDA = 'VENCIDA'
    ESTADO_ANULADA = 'ANULADA'
    ESTADO_CHOICES = (
        (ESTADO_ABIERTA, 'Abierta'),
        (ESTADO_PARCIAL, 'Parcial'),
        (ESTADO_PAGADA, 'Pagada'),
        (ESTADO_VENCIDA, 'Vencida'),
        (ESTADO_ANULADA, 'Anulada'),
    )

    cliente = models.ForeignKey(
        'clientes.Cliente',
        on_delete=models.PROTECT,
        related_name='cuentas_por_cobrar',
    )
    venta = models.OneToOneField(
        'ventas.Venta',
        on_delete=models.PROTECT,
        related_name='cuenta_por_cobrar',
    )
    metodo_plazo = models.ForeignKey(
        MetodoPlazoCredito,
        on_delete=models.PROTECT,
        related_name='cuentas',
    )
    total = models.DecimalField(max_digits=12, decimal_places=2)
    monto_inicial = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    # Capital financiado (total - inicial), sin interes. El interes es un cargo
    # financiero que vive solo en la CxC: venta.total y Pago(CREDITO) siguen en capital.
    saldo_original = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    interes_porcentaje = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00')), MaxValueValidator(Decimal('100.00'))],
    )
    monto_interes = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    saldo = models.DecimalField(max_digits=12, decimal_places=2)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default=ESTADO_ABIERTA, db_index=True)
    fecha_emision = models.DateField(default=timezone.localdate)
    fecha_limite = models.DateField(db_index=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='cuentas_cxc_creadas',
    )
    override_autorizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='overrides_credito_autorizados',
        blank=True,
        null=True,
    )
    motivo_override = models.CharField(max_length=250, blank=True)
    sucursal = models.ForeignKey(
        'sucursales.Sucursal',
        on_delete=models.PROTECT,
        related_name='cuentas_por_cobrar',
        blank=True,
        null=True,
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Cuenta por cobrar'
        verbose_name_plural = 'Cuentas por cobrar'
        ordering = ['-fecha_emision', '-id']
        indexes = [
            models.Index(fields=['cliente', 'estado']),
            models.Index(fields=['estado', 'fecha_limite']),
            models.Index(fields=['sucursal', 'estado']),
        ]

    def __str__(self):
        return f'CXC {self.venta.numero_venta} - {self.cliente.nombre}'

    @property
    def esta_abierta(self):
        return self.estado in (self.ESTADO_ABIERTA, self.ESTADO_PARCIAL, self.ESTADO_VENCIDA)

    @property
    def monto_financiado(self):
        """Deuda real del cliente al emitir la cuenta: capital + interes."""
        return self.saldo_original + self.monto_interes

    @property
    def esta_vencida(self):
        """Cuenta con saldo pendiente cuya fecha limite ya paso.

        El campo `estado` solo se recalcula por eventos (abono/anulacion),
        asi que una cuenta ABIERTA/PARCIAL puede estar vencida de hecho sin
        que `estado == VENCIDA`. Esta propiedad calcula el vencimiento real
        contra la fecha de hoy.
        """
        return self.esta_abierta and self.fecha_limite < timezone.localdate()

    def recalcular_estado(self, guardar=True):
        if self.estado == self.ESTADO_ANULADA:
            return self.estado
        if self.saldo <= Decimal('0.00'):
            self.estado = self.ESTADO_PAGADA
        elif self.saldo < self.monto_financiado:
            self.estado = self.ESTADO_PARCIAL
        elif self.fecha_limite < timezone.localdate():
            self.estado = self.ESTADO_VENCIDA
        else:
            self.estado = self.ESTADO_ABIERTA
        if guardar:
            self.save(update_fields=['estado', 'saldo', 'fecha_modificacion'])
        return self.estado

    def marcar_anulada(self, guardar=True):
        self.estado = self.ESTADO_ANULADA
        self.saldo = Decimal('0.00')
        if guardar:
            self.save(update_fields=['estado', 'saldo', 'fecha_modificacion'])
        self.cuotas.update(estado=CuotaCxC.ESTADO_ANULADA, saldo=Decimal('0.00'))


class CuotaCxC(models.Model):
    ESTADO_PENDIENTE = 'PENDIENTE'
    ESTADO_PARCIAL = 'PARCIAL'
    ESTADO_PAGADA = 'PAGADA'
    ESTADO_VENCIDA = 'VENCIDA'
    ESTADO_ANULADA = 'ANULADA'
    ESTADO_CHOICES = (
        (ESTADO_PENDIENTE, 'Pendiente'),
        (ESTADO_PARCIAL, 'Parcial'),
        (ESTADO_PAGADA, 'Pagada'),
        (ESTADO_VENCIDA, 'Vencida'),
        (ESTADO_ANULADA, 'Anulada'),
    )

    cuenta = models.ForeignKey(CuentaPorCobrar, on_delete=models.CASCADE, related_name='cuotas')
    numero = models.PositiveIntegerField()
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    saldo = models.DecimalField(max_digits=12, decimal_places=2)
    fecha_vencimiento = models.DateField(db_index=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default=ESTADO_PENDIENTE, db_index=True)
    fecha_pago = models.DateTimeField(blank=True, null=True)

    class Meta:
        verbose_name = 'Cuota CxC'
        verbose_name_plural = 'Cuotas CxC'
        ordering = ['cuenta', 'numero']
        constraints = [
            models.UniqueConstraint(fields=['cuenta', 'numero'], name='unique_cuota_por_cuenta_numero'),
        ]
        indexes = [
            models.Index(fields=['estado', 'fecha_vencimiento']),
            models.Index(fields=['cuenta', 'estado']),
        ]

    def __str__(self):
        return f'Cuota {self.numero} - {self.cuenta}'

    def recalcular_estado(self, guardar=True):
        if self.estado == self.ESTADO_ANULADA:
            return self.estado
        if self.saldo <= Decimal('0.00'):
            self.estado = self.ESTADO_PAGADA
            if self.fecha_pago is None:
                self.fecha_pago = timezone.now()
        elif self.saldo < self.monto:
            self.estado = self.ESTADO_PARCIAL
        elif self.fecha_vencimiento < timezone.localdate():
            self.estado = self.ESTADO_VENCIDA
        else:
            self.estado = self.ESTADO_PENDIENTE
        if guardar:
            self.save(update_fields=['estado', 'saldo', 'fecha_pago'])
        return self.estado


class PagoCxC(models.Model):
    METODO_EFECTIVO = 'EFECTIVO'
    METODO_TRANSFERENCIA = 'TRANSFERENCIA'
    METODO_TARJETA = 'TARJETA'
    METODO_CHOICES = (
        (METODO_EFECTIVO, 'Efectivo'),
        (METODO_TRANSFERENCIA, 'Transferencia'),
        (METODO_TARJETA, 'Tarjeta'),
    )

    ESTADO_APLICADO = 'APLICADO'
    ESTADO_ANULADO = 'ANULADO'
    ESTADO_CHOICES = (
        (ESTADO_APLICADO, 'Aplicado'),
        (ESTADO_ANULADO, 'Anulado'),
    )

    cuenta = models.ForeignKey(CuentaPorCobrar, on_delete=models.PROTECT, related_name='pagos_cxc')
    metodo = models.CharField(max_length=20, choices=METODO_CHOICES)
    monto = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    referencia = models.CharField(max_length=100, blank=True)
    fecha_pago = models.DateTimeField(default=timezone.now, db_index=True)
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='pagos_cxc_registrados',
    )
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default=ESTADO_APLICADO)
    aplicaciones = models.JSONField(default=list, blank=True)
    notas = models.TextField(blank=True)
    anulado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='pagos_cxc_anulados',
        blank=True,
        null=True,
    )
    fecha_anulacion = models.DateTimeField(blank=True, null=True)
    motivo_anulacion = models.CharField(max_length=250, blank=True)

    class Meta:
        verbose_name = 'Pago CxC'
        verbose_name_plural = 'Pagos CxC'
        ordering = ['-fecha_pago']
        indexes = [
            models.Index(fields=['metodo', 'fecha_pago']),
            models.Index(fields=['registrado_por', 'fecha_pago']),
            models.Index(fields=['cuenta', 'estado']),
        ]

    def __str__(self):
        return f'Pago CxC {self.cuenta.venta.numero_venta} - {self.monto}'
