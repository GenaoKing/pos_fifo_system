"""
apps/caja/models.py
Modulo de Arqueo y Gestion de Caja

Flujo:
1. Admin/Cajera abre turno con fondo inicial
2. Se registran ventas normalmente (vinculadas al turno activo)
3. Durante el turno: retiros, gastos menores, ingresos de fondos
4. Al cerrar: cajera cuenta efectivo fisico, sistema compara con esperado
5. Se registra diferencia (sobrante/faltante)
"""

import uuid
from django.db import models
from django.core.validators import MinValueValidator
from django.conf import settings
from decimal import Decimal
from django.utils import timezone


class Caja(models.Model):
    """
    Caja fisica del negocio.
    """
    nombre = models.CharField(
        max_length=50,
        verbose_name='Nombre',
        help_text='Ej: Caja 1, Caja Principal'
    )

    descripcion = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        verbose_name='Descripcion'
    )

    activa = models.BooleanField(
        default=True,
        verbose_name='Activa'
    )

    sucursal = models.ForeignKey(
        'sucursales.Sucursal',
        on_delete=models.PROTECT,
        related_name='cajas',
        verbose_name='Sucursal',
        blank=True,
        null=True,
        help_text='Sucursal a la que pertenece esta caja. Null para instalaciones legacy.'
    )

    # Identidad ESTABLE para el sync.
    #
    # El cloud resolvia la caja por `(nombre, sucursal)`, y `nombre` es un
    # CharField mutable sin unicidad. Renombrar una caja entre la apertura y el
    # cierre partia el turno en dos: el movimiento no encontraba el turno viejo
    # y el cierre creaba por fallback otro turno bajo la caja nueva. El portal
    # mostraba una caja eternamente abierta y otra cerrada con el mismo
    # efectivo. `nombre` queda como atributo mutable; la identidad es esto.
    origen_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        verbose_name='Identidad de sync',
        help_text='Identidad estable de esta caja entre sucursal y cloud.',
    )

    fecha_creacion = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de Creacion'
    )

    class Meta:
        verbose_name = 'Caja'
        verbose_name_plural = 'Cajas'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre

    def turno_activo(self):
        """Retorna el turno abierto de esta caja, o None"""
        return self.turnos.filter(estado='ABIERTO').first()


class TurnoCaja(models.Model):
    """
    Un turno/sesion de caja.
    Solo puede haber 1 turno abierto por caja a la vez.
    Solo puede haber 1 turno abierto por usuario a la vez.
    """
    ESTADOS = [
        ('ABIERTO', 'Abierto'),
        ('CERRADO', 'Cerrado'),
    ]

    caja = models.ForeignKey(
        Caja,
        on_delete=models.PROTECT,
        related_name='turnos',
        verbose_name='Caja'
    )

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='turnos_caja',
        verbose_name='Cajero/a'
    )

    # Apertura
    fecha_apertura = models.DateTimeField(
        default=timezone.now,
        verbose_name='Fecha de Apertura'
    )

    fondo_apertura = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Fondo de Apertura',
        help_text='Efectivo inicial en caja al abrir turno'
    )

    # Cierre (se llenan al cerrar)
    fecha_cierre = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Fecha de Cierre'
    )

    monto_contado = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        blank=True,
        null=True,
        verbose_name='Efectivo Contado',
        help_text='Lo que la cajera cuenta fisicamente al cerrar'
    )

    monto_esperado = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        blank=True,
        null=True,
        verbose_name='Efectivo Esperado',
        help_text='Calculado: fondo + ventas_efectivo - retiros - gastos + ingresos'
    )

    diferencia = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        blank=True,
        null=True,
        verbose_name='Diferencia',
        help_text='Positivo = sobrante, Negativo = faltante'
    )

    estado = models.CharField(
        max_length=10,
        choices=ESTADOS,
        default='ABIERTO',
        verbose_name='Estado'
    )

    notas_apertura = models.TextField(
        blank=True,
        null=True,
        verbose_name='Notas de Apertura'
    )

    notas_cierre = models.TextField(
        blank=True,
        null=True,
        verbose_name='Notas de Cierre'
    )

    cerrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='turnos_cerrados',
        blank=True,
        null=True,
        verbose_name='Cerrado por',
        help_text='Puede ser el mismo cajero o un admin'
    )

    class Meta:
        verbose_name = 'Turno de Caja'
        verbose_name_plural = 'Turnos de Caja'
        ordering = ['-fecha_apertura']
        indexes = [
            models.Index(fields=['estado']),
            models.Index(fields=['fecha_apertura']),
            models.Index(fields=['usuario', 'estado']),
        ]
        constraints = [
            # Un usuario solo puede tener 1 turno abierto
            models.UniqueConstraint(
                fields=['usuario'],
                condition=models.Q(estado='ABIERTO'),
                name='unique_turno_abierto_por_usuario'
            ),
            # Una caja solo puede tener 1 turno abierto
            models.UniqueConstraint(
                fields=['caja'],
                condition=models.Q(estado='ABIERTO'),
                name='unique_turno_abierto_por_caja'
            ),
        ]

    def __str__(self):
        estado = "ABIERTO" if self.estado == 'ABIERTO' else f"CERRADO {self.fecha_cierre.strftime('%d/%m') if self.fecha_cierre else ''}"
        return f"{self.caja.nombre} - {self.usuario.get_short_name()} - {estado}"

    def calcular_esperado(self):
        """
        Calcula el efectivo esperado en caja.

        Formula:
        fondo_apertura
        + ventas en efectivo del turno
        - retiros
        - gastos
        + ingresos

        ATRIBUCION: se prefiere el vinculo EXACTO (`Pago.turno_caja`). La
        heuristica por usuario+fechas solo cubre los pagos historicos, que son
        los anteriores a que existiera ese campo.

        La heuristica no era exacta: atribuia por coincidencia temporal, asi que
        si el mismo usuario operaba contra otra sucursal durante el rango, este
        cierre sumaba efectivo ajeno; y un cobro que competia con el cierre
        caia de un lado u otro del corte segun el orden de commit.
        """
        from apps.ventas.models import Pago
        from apps.cuentas_por_cobrar.models import PagoCxC

        # --- Pagos EXPLICITAMENTE atados a este turno ---
        efectivo_ventas = Pago.objects.filter(
            turno_caja=self,
            metodo='EFECTIVO',
            venta__estado='COMPLETADA',
        ).aggregate(total=models.Sum('monto'))['total'] or Decimal('0.00')

        # --- Historicos: sin turno, se reconstruyen por usuario+ventana ---
        filtro_turno = {
            'turno_caja__isnull': True,
            'venta__fecha_venta__gte': self.fecha_apertura,
            'venta__estado': 'COMPLETADA',
            'metodo': 'EFECTIVO',
            'venta__usuario': self.usuario,
        }
        if self.fecha_cierre:
            filtro_turno['venta__fecha_venta__lte'] = self.fecha_cierre

        efectivo_ventas += Pago.objects.filter(
            **filtro_turno
        ).aggregate(
            total=models.Sum('monto')
        )['total'] or Decimal('0.00')

        efectivo_cxc = PagoCxC.objects.filter(
            turno_caja=self,
            metodo='EFECTIVO',
            estado='APLICADO',
        ).aggregate(total=models.Sum('monto'))['total'] or Decimal('0.00')

        filtro_cxc = {
            'turno_caja__isnull': True,
            'fecha_pago__gte': self.fecha_apertura,
            'estado': 'APLICADO',
            'metodo': 'EFECTIVO',
            'registrado_por': self.usuario,
        }
        if self.fecha_cierre:
            filtro_cxc['fecha_pago__lte'] = self.fecha_cierre

        efectivo_cxc += PagoCxC.objects.filter(**filtro_cxc).aggregate(
            total=models.Sum('monto')
        )['total'] or Decimal('0.00')

        # Movimientos de este turno
        movimientos = self.movimientos.aggregate(
            retiros=models.Sum(
                'monto',
                filter=models.Q(tipo='RETIRO'),
                default=Decimal('0.00')
            ),
            gastos=models.Sum(
                'monto',
                filter=models.Q(tipo='GASTO'),
                default=Decimal('0.00')
            ),
            ingresos=models.Sum(
                'monto',
                filter=models.Q(tipo='INGRESO'),
                default=Decimal('0.00')
            ),
        )

        esperado = (
            self.fondo_apertura
            + efectivo_ventas
            + efectivo_cxc
            - (movimientos['retiros'] or Decimal('0.00'))
            - (movimientos['gastos'] or Decimal('0.00'))
            + (movimientos['ingresos'] or Decimal('0.00'))
        )

        return {
            'fondo_apertura': self.fondo_apertura,
            'efectivo_ventas': efectivo_ventas,
            'efectivo_cxc': efectivo_cxc,
            'retiros': movimientos['retiros'] or Decimal('0.00'),
            'gastos': movimientos['gastos'] or Decimal('0.00'),
            'ingresos': movimientos['ingresos'] or Decimal('0.00'),
            'esperado': esperado,
        }

    def cerrar(self, monto_contado, cerrado_por, notas=None):
        """
        Cierra el turno, calcula diferencia.
        """
        calculo = self.calcular_esperado()

        self.fecha_cierre = timezone.now()
        self.monto_contado = monto_contado
        self.monto_esperado = calculo['esperado']
        self.diferencia = monto_contado - calculo['esperado']
        self.estado = 'CERRADO'
        self.cerrado_por = cerrado_por
        self.notas_cierre = notas
        self.save()

        return calculo


class MovimientoCaja(models.Model):
    """
    Movimientos de caja que no son ventas:
    - RETIRO: Admin saca efectivo de la caja
    - GASTO: Compra menor (hielo, delivery, suministros)
    - INGRESO: Admin mete fondo adicional (menudo, cambio)
    """
    TIPOS = [
        ('RETIRO', 'Retiro de Efectivo'),
        ('GASTO', 'Gasto Menor'),
        ('INGRESO', 'Ingreso de Fondos'),
    ]

    turno = models.ForeignKey(
        TurnoCaja,
        on_delete=models.PROTECT,
        related_name='movimientos',
        verbose_name='Turno'
    )

    tipo = models.CharField(
        max_length=10,
        choices=TIPOS,
        verbose_name='Tipo'
    )

    monto = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name='Monto'
    )

    descripcion = models.CharField(
        max_length=200,
        verbose_name='Descripcion',
        help_text='Motivo del movimiento'
    )

    # Quien lo registra (la cajera)
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='movimientos_caja_registrados',
        verbose_name='Registrado por'
    )

    # Quien lo autoriza (el admin, via soft-login)
    # Solo requerido para RETIRO. Gastos e ingresos pueden no necesitarlo.
    autorizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='movimientos_caja_autorizados',
        blank=True,
        null=True,
        verbose_name='Autorizado por',
        help_text='Admin que autorizo via soft-login (requerido para retiros)'
    )

    fecha = models.DateTimeField(
        default=timezone.now,
        verbose_name='Fecha'
    )

    class Meta:
        verbose_name = 'Movimiento de Caja'
        verbose_name_plural = 'Movimientos de Caja'
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['turno', 'tipo']),
            models.Index(fields=['fecha']),
        ]

    def __str__(self):
        signo = '-' if self.tipo in ('RETIRO', 'GASTO') else '+'
        return f"{self.get_tipo_display()} {signo}${self.monto} - {self.descripcion[:30]}"


def turno_abierto_de(usuario, sucursal=None):
    """
    Turno de caja ABIERTO de un usuario, o None.

    Lo usan los services de venta y de cobro CxC para atar cada pago en
    efectivo a la sesion de caja que lo recibio, en vez de dejar que el cierre
    lo adivine despues por usuario y fecha.

    Devuelve None sin fallar: un cobro sin turno abierto sigue siendo posible
    (canales sin caja fisica, instalaciones que no usan arqueo). Lo que cambia
    es que ahora se sabe cual es cual.
    """
    qs = TurnoCaja.objects.filter(usuario=usuario, estado='ABIERTO')
    if sucursal is not None:
        qs = qs.filter(models.Q(caja__sucursal=sucursal) | models.Q(caja__sucursal__isnull=True))
    return qs.select_related('caja').first()
