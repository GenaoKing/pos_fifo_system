from django.db import models, transaction, IntegrityError
from django.core.validators import MinValueValidator
from django.conf import settings
from decimal import Decimal
from datetime import datetime, timedelta, timezone
import pytz
import django.utils.timezone as django_timezone

# Reintentos de asignacion de numero_venta ante colision con otra caja.
# Ver Venta.save(): la carrera es corta (dos INSERT del mismo prefijo en el
# mismo instante), por lo que un puñado de reintentos la cubre de sobra.
MAX_INTENTOS_NUMERO_VENTA = 5

class Venta(models.Model):
    """
    Cabecera de venta
    """
    ESTADOS = [
        ('COMPLETADA', 'Completada'),
        ('ANULADA', 'Anulada'),
    ]

    CONDICIONES_PAGO = [
        ('CONTADO', 'Contado'),
        ('CREDITO', 'Credito'),
    ]
    
    numero_venta = models.CharField(
        max_length=50,
        unique=True,
        verbose_name='Número de Venta'
    )
    
    fecha_venta = models.DateTimeField(
        #auto_now_add=True,
        verbose_name='Fecha de Venta'
    )
    
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='ventas',
        verbose_name='Usuario'
    )

    cliente = models.ForeignKey(
        'clientes.Cliente',
        on_delete=models.PROTECT,
        related_name='ventas',
        verbose_name='Cliente',
        blank=True,
        null=True,
        help_text='Null = Cliente Contado'
    )
    
    sucursal = models.ForeignKey(
        'sucursales.Sucursal',
        on_delete=models.PROTECT,
        related_name='ventas',
        verbose_name='Sucursal',
        blank=True,
        null=True,
        help_text='Sucursal donde se realizo la venta. Null para ventas legacy.'
    )

    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Subtotal'
    )
    
    descuento_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Descuento Total'
    )
    
    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name='Total'
    )

    condicion_pago = models.CharField(
        max_length=20,
        choices=CONDICIONES_PAGO,
        default='CONTADO',
        verbose_name='Condicion de Pago',
        help_text='Contado para ventas pagadas al cierre; credito si genera CxC.'
    )
    
    estado = models.CharField(
        max_length=20,
        choices=ESTADOS,
        default='COMPLETADA',
        verbose_name='Estado'
    )
    
    notas = models.TextField(
        blank=True,
        null=True,
        verbose_name='Notas'
    )
    
    # Auditoría de anulaciones
    fecha_anulacion = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Fecha de Anulación'
    )
    
    anulada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='ventas_anuladas',
        blank=True,
        null=True,
        verbose_name='Anulada por'
    )
    
    motivo_anulacion = models.TextField(
        blank=True,
        null=True,
        verbose_name='Motivo de Anulación'
    )
    
    class Meta:
        verbose_name = 'Venta'
        verbose_name_plural = 'Ventas'
        ordering = ['-fecha_venta']
        indexes = [
            models.Index(fields=['numero_venta']),
            models.Index(fields=['fecha_venta']),
            models.Index(fields=['estado']),
        ]
    
    def __str__(self):
        return f"Venta {self.numero_venta} - ${self.total}"
    
    def save(self, *args, **kwargs):
        # PRIMERO: Establecer fecha_venta si es nueva venta
        if not self.pk and not self.fecha_venta:
            from django.utils import timezone as django_timezone
            santo_domingo_tz = pytz.timezone('America/Santo_Domingo')
            self.fecha_venta = django_timezone.now().astimezone(santo_domingo_tz)
            print(f"Fecha de venta establecida a {self.fecha_venta} (Santo Domingo)")

        # SEGUNDO: Generar número de venta basado en fecha_venta.
        # Si ya viene asignado (replicación cloud, correcciones), no se toca.
        if self.numero_venta:
            return super().save(*args, **kwargs)

        prefijo = self._prefijo_numero_venta()

        # `numero_venta` es unique y la secuencia se calcula leyendo la tabla,
        # asi que dos cajas cerrando a la vez pueden proponer el mismo numero.
        # En vez de reventar con un 500 despues de que el cajero ya cobro,
        # reintentamos dentro de un savepoint: el INSERT fallido no aborta la
        # transaccion de negocio y la siguiente vuelta lee el numero ya tomado.
        for intento in range(MAX_INTENTOS_NUMERO_VENTA):
            self.numero_venta = self._siguiente_numero_venta(prefijo)
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError:
                if intento == MAX_INTENTOS_NUMERO_VENTA - 1:
                    raise
                self.numero_venta = ''

    def _prefijo_numero_venta(self):
        """
        Prefijo del numero de venta. Con sucursal asignada incluye su codigo,
        lo que hace el numero unico entre sucursales del mismo negocio (el
        cloud deduplica por `numero_venta`). Sin sucursal cae al formato
        legacy `V-<fecha>`.
        """
        fecha_str = self.fecha_venta.strftime('%Y%m%d')
        if self.sucursal_id:
            return f'{self.sucursal.codigo}-V{fecha_str}'
        return f'V-{fecha_str}'

    @staticmethod
    def _siguiente_numero_venta(prefijo):
        """
        Siguiente correlativo del prefijo.

        Usa el MAXIMO sufijo existente, no `count()`: contar filas reutiliza un
        numero ya emitido en cuanto la secuencia tiene un hueco (una venta
        borrada o corregida a mano), y ahi la colision es permanente en vez de
        transitoria.
        """
        numeros = Venta.objects.filter(
            numero_venta__startswith=prefijo
        ).values_list('numero_venta', flat=True)

        ultimo = 0
        for numero in numeros:
            sufijo = numero.rsplit('-', 1)[-1]
            if sufijo.isdigit():
                ultimo = max(ultimo, int(sufijo))

        return f'{prefijo}-{str(ultimo + 1).zfill(4)}'

    def puede_anularse(self):
        """
        Verifica si la venta esta dentro del plazo de anulacion configurado.

        El plazo sale de `ConfiguracionNegocio.dias_anulacion` — la misma
        fuente que muestra la UI y que cita el mensaje de error del service.
        La comparacion es entre datetimes *aware*: mezclar `datetime.now()`
        naive con la fecha de venta corre el vencimiento tantas horas como
        offset tenga el host (en un host UTC, 4 horas respecto a Santo Domingo).
        """
        if self.estado == 'ANULADA':
            return False

        from apps.configuracion.utils import get_config

        try:
            dias_permitidos = get_config().dias_anulacion
        except Exception:
            # Sin configuracion cargable (instalacion a medio migrar) caemos al
            # default historico en vez de bloquear la anulacion.
            dias_permitidos = getattr(settings, 'ANULACION_DIAS_PERMITIDOS', 15)

        fecha_limite = self.fecha_venta + timedelta(days=dias_permitidos)

        return django_timezone.now() <= fecha_limite


    def calcular_totales(self):
        """Recalcula totales basado en detalles"""
        detalles = self.detalles.all()
        
        self.subtotal = sum(d.subtotal for d in detalles)
        self.descuento_total = sum(d.descuento_monto for d in detalles)
        self.total = sum(d.total_linea for d in detalles)
        
        return {
            'subtotal': self.subtotal,
            'descuento_total': self.descuento_total,
            'total': self.total
        }


class DetalleVenta(models.Model):
    """
    Líneas de detalle de venta
    """
    venta = models.ForeignKey(
        Venta,
        on_delete=models.CASCADE,
        related_name='detalles',
        verbose_name='Venta'
    )
    
    producto = models.ForeignKey(
        'productos.Producto',
        on_delete=models.PROTECT,
        related_name='detalles_venta',
        verbose_name='Producto'
    )
    
    cantidad = models.IntegerField(
        validators=[MinValueValidator(1)],
        verbose_name='Cantidad'
    )
    
    precio_unitario = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name='Precio Unitario'
    )
    
    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name='Subtotal'
    )
    
    # Descuento por línea (monto fijo)
    descuento_monto = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Descuento (Monto)'
    )
    
    descuento_porcentaje = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Descuento (%)'
    )
    
    total_linea = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name='Total Línea'
    )
    
    # Costo FIFO para calcular margen
    costo_fifo = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Costo FIFO'
    )
    
    class Meta:
        verbose_name = 'Detalle de Venta'
        verbose_name_plural = 'Detalles de Venta'
    
    def __str__(self):
        return f"{self.producto.nombre} × {self.cantidad}"
    
    def save(self, *args, **kwargs):
        # Calcular subtotal
        self.subtotal = self.cantidad * self.precio_unitario
        
        # Calcular porcentaje de descuento
        if self.subtotal > 0:
            self.descuento_porcentaje = (self.descuento_monto / self.subtotal) * 100
        
        # Calcular total línea
        self.total_linea = self.subtotal - self.descuento_monto
        
        super().save(*args, **kwargs)
    
    def get_margen_bruto(self):
        """Margen bruto de la línea"""
        if self.costo_fifo > 0:
            return self.total_linea - self.costo_fifo
        return Decimal('0.00')
    
    def get_margen_porcentaje(self):
        """Porcentaje de margen"""
        if self.total_linea > 0:
            return (self.get_margen_bruto() / self.total_linea) * 100
        return Decimal('0.00')


class Pago(models.Model):
    """
    Pagos de venta - permite múltiples pagos (mixto)
    """
    METODOS = [
        ('EFECTIVO', 'Efectivo'),
        ('TRANSFERENCIA', 'Transferencia'),
        ('TARJETA', 'Tarjeta'),
        ('CREDITO', 'Credito'),
    ]
    
    venta = models.ForeignKey(
        Venta,
        on_delete=models.CASCADE,
        related_name='pagos',
        verbose_name='Venta'
    )
    
    metodo = models.CharField(
        max_length=20,
        choices=METODOS,
        verbose_name='Metodo de Pago'
    )
    
    monto = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name='Monto'
    )
    
    referencia = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='Referencia'
    )
    
    fecha_pago = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de Pago'
    )

    # Turno de caja que recibio este efectivo.
    #
    # No existia: la pertenencia se RECONSTRUIA despues, filtrando por usuario
    # y rango de fechas en `TurnoCaja.calcular_esperado()`. Eso significa que un
    # pago se atribuia por coincidencia temporal, no por el hecho operativo: si
    # el usuario operaba contra otra sucursal en ese rango, el cierre sumaba
    # efectivo ajeno, y un cobro que competia con el cierre caia de un lado u
    # otro del corte segun el orden de commit.
    #
    # Null para pagos historicos y para los que no pasan por una caja fisica
    # (replicacion cloud, canales sin turno). `calcular_esperado` prefiere este
    # vinculo y solo cae a la heuristica para los historicos.
    turno_caja = models.ForeignKey(
        'caja.TurnoCaja',
        on_delete=models.PROTECT,
        related_name='%(class)s_recibidos',
        null=True,
        blank=True,
        verbose_name='Turno de caja',
        help_text='Turno que recibio el efectivo. Null si no paso por caja.',
    )

    
    class Meta:
        verbose_name = 'Pago'
        verbose_name_plural = 'Pagos'
        ordering = ['fecha_pago']
    
    def __str__(self):
        return f"{self.get_metodo_display()} - ${self.monto}"


"""
Modelo para Financiacion Cooperativa
Se agrega como extension de la app ventas.

Una venta con financiacion cooperativa es una venta normal
que adicionalmente genera un PDF formal tipo factura
con datos del cliente proporcionados por la cooperativa.
"""


class FinanciacionCooperativa(models.Model):
    """
    Datos adicionales para ventas financiadas por cooperativa.
    La venta se procesa normal (afecta inventario inmediatamente).
    Este modelo solo almacena los datos extra para el PDF formal.
    """

    venta = models.OneToOneField(
        'ventas.Venta',
        on_delete=models.CASCADE,
        related_name='financiacion',
        verbose_name='Venta'
    )

    # Datos del cliente (proporcionados por la cooperativa)
    nombre_cliente = models.CharField(
        max_length=200,
        verbose_name='Nombre del Cliente'
    )

    cedula_cliente = models.CharField(
        max_length=20,
        verbose_name='Cedula del Cliente'
    )

    telefono_cliente = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        verbose_name='Telefono del Cliente'
    )

    direccion_cliente = models.TextField(
        blank=True,
        null=True,
        verbose_name='Direccion del Cliente'
    )

    # Datos de la cooperativa (fija, pero guardamos por trazabilidad)
    nombre_cooperativa = models.CharField(
        max_length=200,
        default='',
        verbose_name='Nombre de la Cooperativa'
    )

    codigo_aprobacion = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name='Codigo de Aprobacion',
        help_text='Codigo de aprobacion de la cooperativa'
    )

    notas = models.TextField(
        blank=True,
        null=True,
        verbose_name='Notas Adicionales'
    )

    # PDF generado
    pdf_generado = models.BooleanField(
        default=False,
        verbose_name='PDF Generado'
    )

    fecha_creacion = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de Registro'
    )

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='financiaciones',
        verbose_name='Registrado por'
    )

    class Meta:
        verbose_name = 'Financiacion Cooperativa'
        verbose_name_plural = 'Financiaciones Cooperativas'
        ordering = ['-fecha_creacion']

    def __str__(self):
        return f"Financiacion {self.venta.numero_venta} - {self.nombre_cliente}"
