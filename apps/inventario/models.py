from django.db import models, transaction, IntegrityError
from django.core.validators import MinValueValidator
from decimal import Decimal
from datetime import datetime
from django.conf import settings

# Reintentos de asignacion de correlativo ante colision con otra caja/proceso.
MAX_INTENTOS_CORRELATIVO = 5


def _siguiente_correlativo(modelo, campo, prefijo, ancho):
    """
    Siguiente correlativo de un prefijo, a partir del MAXIMO sufijo existente.

    NO cuenta filas. `count() + 1` reutiliza un numero ya emitido en cuanto la
    secuencia tiene un hueco — y la edicion de compras borra lotes, asi que los
    huecos son normales, no excepcionales. Ahi la colision es permanente: el
    INSERT falla siempre, sin concurrencia de por medio.
    """
    numeros = modelo.objects.filter(
        **{f'{campo}__startswith': prefijo}
    ).values_list(campo, flat=True)

    ultimo = 0
    for numero in numeros:
        sufijo = numero.rsplit('-', 1)[-1]
        if sufijo.isdigit():
            ultimo = max(ultimo, int(sufijo))

    return f'{prefijo}-{str(ultimo + 1).zfill(ancho)}'


def _guardar_con_correlativo(instancia, campo, prefijo, modelo, ancho, guardar):
    """
    Asigna el correlativo y guarda, reintentando ante colision de unicidad.

    Dos procesos pueden proponer el mismo numero a la vez. En vez de reventar
    con IntegrityError (500 con la compra ya capturada), se reintenta dentro de
    un savepoint: el INSERT fallido no aborta la transaccion de negocio y la
    vuelta siguiente lee el numero que el otro acaba de tomar.
    """
    for intento in range(MAX_INTENTOS_CORRELATIVO):
        setattr(instancia, campo, _siguiente_correlativo(modelo, campo, prefijo, ancho))
        try:
            with transaction.atomic():
                return guardar()
        except IntegrityError:
            if intento == MAX_INTENTOS_CORRELATIVO - 1:
                raise
            setattr(instancia, campo, '')



class Compra(models.Model):
    """
    Registro de compras a proveedores
    """
    numero_compra = models.CharField(
        max_length=50,
        unique=True,
        verbose_name='Número de Compra'
    )
    
    proveedor = models.CharField(
        max_length=200,
        verbose_name='Proveedor'
    )
    
    fecha_compra = models.DateTimeField(
        verbose_name='Fecha de Compra',
        auto_now_add=True
    )

    numero_factura = models.CharField(
        max_length=50,
        blank=True, 
        null=True)
    
    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name='Total'
    )
    
    notas = models.TextField(
        blank=True,
        null=True,
        verbose_name='Notas'
    )
    
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='compras',
        verbose_name='Registrado por'
    )

    sucursal = models.ForeignKey(
        'sucursales.Sucursal',
        on_delete=models.PROTECT,
        related_name='compras',
        verbose_name='Sucursal',
        blank=True,
        null=True,
        help_text='Sucursal donde se registro la compra. Null para compras legacy.'
    )
    
    fecha_creacion = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de Registro'
    )
    
    class Meta:
        verbose_name = 'Compra'
        verbose_name_plural = 'Compras'
        ordering = ['-fecha_compra']
        indexes = [
            models.Index(fields=['numero_compra']),
            models.Index(fields=['fecha_compra']),
        ]
    
    def __str__(self):
        return f"Compra {self.numero_compra} - {self.proveedor}"
    
    def save(self, *args, **kwargs):
        if self.numero_compra:
            return super().save(*args, **kwargs)

        prefijo = f'COMP-{datetime.now().strftime("%Y%m%d")}'
        return _guardar_con_correlativo(
            self, 'numero_compra', prefijo, Compra, ancho=5,
            guardar=lambda: super(Compra, self).save(*args, **kwargs),
        )


class DetalleCompra(models.Model):
    """
    Detalle de productos en una compra
    ⭐ Al guardar, auto-genera un Lote
    """
    compra = models.ForeignKey(
        Compra,
        on_delete=models.CASCADE,
        related_name='detalles',
        verbose_name='Compra'
    )
    
    producto = models.ForeignKey(
        'productos.Producto',
        on_delete=models.PROTECT,
        related_name='detalles_compra',
        verbose_name='Producto'
    )
    
    cantidad = models.IntegerField(
        validators=[MinValueValidator(1)],
        verbose_name='Cantidad'
    )
    
    costo_unitario = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name='Costo Unitario'
    )
    
    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name='Subtotal'
    )
    
    class Meta:
        verbose_name = 'Detalle de Compra'
        verbose_name_plural = 'Detalles de Compra'
    
    def __str__(self):
        return f"{self.producto.nombre} - {self.cantidad} unidades"
    
    def save(self, *args, **kwargs):
        # Calcular subtotal
        self.subtotal = self.cantidad * self.costo_unitario
        super().save(*args, **kwargs)

        # Crear el lote SOLO si esta linea todavia no tiene uno.
        #
        # La guarda era `not hasattr(self, '_lote_creado')`, un atributo que
        # solo existia en la instancia en memoria que acababa de crear el lote.
        # Al recargar el detalle desde la BD el atributo se perdia, asi que un
        # `save()` sin cambios intentaba crear un SEGUNDO lote y moria con
        # IntegrityError (`Lote.detalle_compra` es OneToOne). La vista de
        # edicion lo sorteaba asignando el atributo privado a mano.
        #
        # Ahora la guarda es el estado persistente: existe o no existe el lote.
        if not Lote.objects.filter(detalle_compra=self).exists():
            self._crear_lote()

    def _crear_lote(self):
        """
        Crea automáticamente un lote para este detalle
        ⭐ 1 DetalleCompra = 1 Lote + 1 MovimientoLote
        """
        prefijo = f'LOTE-{self.compra.fecha_compra.strftime("%Y%m%d")}'

        lote = Lote(
            producto=self.producto,
            detalle_compra=self,
            fecha_compra=self.compra.fecha_compra,
            cantidad_inicial=self.cantidad,
            cantidad_actual=self.cantidad,
            costo_unitario=self.costo_unitario,
            sucursal=self.compra.sucursal,
        )
        # El correlativo de lote tenia el mismo defecto que el de compra, y aca
        # el hueco es rutina: la edicion de compras borra lotes.
        _guardar_con_correlativo(
            lote, 'numero_lote', prefijo, Lote, ancho=5,
            guardar=lambda: models.Model.save(lote),
        )

        # Crear movimiento inicial
        MovimientoLote.objects.create(
            lote=lote,
            tipo='COMPRA',
            cantidad=self.cantidad,
            cantidad_anterior=0,
            cantidad_nueva=self.cantidad,
            referencia_tipo='Compra',
            referencia_id=self.compra.id,
            usuario=self.compra.usuario,
            notas=f'Compra inicial - {self.compra.numero_compra}'
        )
        return lote






class Lote(models.Model):
    """
    Core del sistema FIFO - cada compra genera un lote
    """
    producto = models.ForeignKey(
        'productos.Producto',
        on_delete=models.PROTECT,
        related_name='lotes',
        verbose_name='Producto'
    )
    

    detalle_compra = models.OneToOneField(
        'DetalleCompra',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lote',
        verbose_name='Detalle de Compra',
        help_text='Línea de compra que generó este lote. Null si la línea se '
                  'corrigió y el lote quedó anulado (su historial se conserva).',
    )

    numero_lote = models.CharField(
        max_length=50,
        unique=True,
        verbose_name='Número de Lote'
    )
    
    fecha_compra = models.DateTimeField(
        verbose_name='Fecha de Compra',
        db_index=True
    )
    
    cantidad_inicial = models.IntegerField(
        validators=[MinValueValidator(1)],
        verbose_name='Cantidad Inicial'
    )
    
    cantidad_actual = models.IntegerField(
        verbose_name='Cantidad Actual'
    )
    
    costo_unitario = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name='Costo Unitario'
    )
    
    activo = models.BooleanField(
        default=True,
        verbose_name='Activo'
    )

    sucursal = models.ForeignKey(
        'sucursales.Sucursal',
        on_delete=models.PROTECT,
        related_name='lotes',
        verbose_name='Sucursal',
        blank=True,
        null=True,
        help_text='Sucursal donde se registro la compra. Null para compras legacy.'
    )
    
    fecha_creacion = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de Creación'
    )
    
    class Meta:
        verbose_name = 'Lote'
        verbose_name_plural = 'Lotes'
        ordering = ['fecha_compra', 'id']  # FIFO: más antiguo primero
        indexes = [
            models.Index(fields=['producto', 'fecha_compra']),
            models.Index(fields=['cantidad_actual']),
            models.Index(fields=['sucursal', 'producto']),
        ]
    
    def __str__(self):
        return f"{self.numero_lote} - {self.producto.nombre} ({self.cantidad_actual}/{self.cantidad_inicial})"
    
    def esta_agotado(self):
        return self.cantidad_actual <= 0
    
    def get_valor_actual(self):
        return self.cantidad_actual * self.costo_unitario


class MovimientoLote(models.Model):
    """
    Historial completo de movimientos de cada lote
    Cada cambio en cantidad_actual genera un registro aquí
    """
    TIPOS_MOVIMIENTO = [
        ('COMPRA', 'Compra Inicial'),
        ('VENTA', 'Venta'),
        ('AJUSTE', 'Ajuste Manual'),
        ('ANULACION', 'Anulación de Venta'),
        ('MERMA', 'Merma'),
        ('DANO', 'Daño'),
    ]
    
    lote = models.ForeignKey(
        'Lote',
        on_delete=models.CASCADE,
        related_name='movimientos',
        verbose_name='Lote'
    )
    
    tipo = models.CharField(
        max_length=20,
        choices=TIPOS_MOVIMIENTO,
        verbose_name='Tipo de Movimiento'
    )
    
    cantidad = models.IntegerField(
        verbose_name='Cantidad',
        help_text='Positivo = entrada, Negativo = salida'
    )
    
    cantidad_anterior = models.IntegerField(
        verbose_name='Cantidad Anterior'
    )
    
    cantidad_nueva = models.IntegerField(
        verbose_name='Cantidad Nueva'
    )
    
    referencia_tipo = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name='Tipo de Referencia',
        help_text='Ej: Venta, Ajuste, etc.'
    )
    
    referencia_id = models.IntegerField(
        blank=True,
        null=True,
        verbose_name='ID de Referencia'
    )
    
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='movimientos_lote',
        verbose_name='Usuario'
    )
    
    notas = models.TextField(
        blank=True,
        null=True,
        verbose_name='Notas'
    )
    
    fecha_creacion = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha del Movimiento'
    )
    
    class Meta:
        verbose_name = 'Movimiento de Lote'
        verbose_name_plural = 'Movimientos de Lote'
        ordering = ['-fecha_creacion']
        indexes = [
            models.Index(fields=['lote', 'tipo']),
            models.Index(fields=['fecha_creacion']),
        ]
    
    def __str__(self):
        signo = '+' if self.cantidad > 0 else ''
        return f"{self.lote.numero_lote} - {self.get_tipo_display()} ({signo}{self.cantidad})"


class AjusteInventario(models.Model):
    """
    Ajustes manuales de inventario
    Merma, daño, conteo físico, correcciones
    """
    TIPOS_AJUSTE = [
        ('MERMA', 'Merma'),
        ('DANO', 'Daño'),
        ('CONTEO', 'Ajuste por Conteo'),
        ('CORRECCION', 'Corrección'),
        ('DEVOLUCION', 'Devolución'),
    ]
    
    lote = models.ForeignKey(
        'Lote',
        on_delete=models.PROTECT,
        related_name='ajustes',
        verbose_name='Lote'
    )
    
    tipo = models.CharField(
        max_length=20,
        choices=TIPOS_AJUSTE,
        verbose_name='Tipo de Ajuste'
    )
    
    cantidad = models.IntegerField(
        verbose_name='Cantidad',
        help_text='Positivo = agregar, Negativo = quitar'
    )
    
    motivo = models.TextField(
        verbose_name='Motivo'
    )
    
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='ajustes_inventario',
        verbose_name='Realizado por'
    )
    
    fecha_ajuste = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha del Ajuste'
    )
    
    class Meta:
        verbose_name = 'Ajuste de Inventario'
        verbose_name_plural = 'Ajustes de Inventario'
        ordering = ['-fecha_ajuste']
    
    def __str__(self):
        return f"{self.get_tipo_display()} - {self.lote.numero_lote} ({self.cantidad})"
    
    # NOTA: este modelo es un REGISTRO, no un aplicador.
    #
    # `save()` solia mutar el lote y crear un MovimientoLote. Eso tenia dos
    # consecuencias graves:
    #
    #   1. El endpoint de ajuste creaba ADEMAS su propio movimiento con el tipo
    #      real (MERMA/DANO), asi que un solo ajuste dejaba DOS movimientos en
    #      el ledger, con tipos distintos.
    #   2. `save()` no distinguia alta de actualizacion: corregir el motivo de
    #      un ajuste ya aplicado volvia a descontar la cantidad completa del
    #      stock y agregaba otro movimiento.
    #
    # La aplicacion del ajuste vive ahora en
    # `apps.inventario.services.registrar_ajuste_service`, que bloquea el lote,
    # escribe UN movimiento y deja todo en una transaccion. Guardar un
    # AjusteInventario ya no mueve inventario.