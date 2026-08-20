"""
apps/sync/models.py

Modelos locales de la cola de sincronizacion.

Estos modelos viven en la BD LOCAL de cada sucursal. No son parte de la BD
cloud. Sirven como:

    EventoSync      -> cola outbound de cambios que hay que empujar a la nube
    VersionMaestro  -> cursor de sync para cada tabla de datos maestros
    LogSync         -> bitacora de cada corrida del engine (diagnostico)

Diseno:
- EventoSync.hash_payload se usa para idempotencia: si el cloud ya vio ese hash,
  responde "confirmado" sin duplicar. Permite retries seguros.
- VersionMaestro guarda el timestamp del registro mas reciente que se bajo en la
  ultima corrida exitosa; la siguiente corrida solo pide cambios desde ese
  timestamp via ?desde=...
- LogSync NO se limpia automaticamente; un command aparte puede purgar los
  mas antiguos de N dias si el volumen se vuelve un problema.
"""
from django.db import models
from django.utils import timezone
from apps.sync.constants import TIPOS_EVENTO


class EventoSync(models.Model):
    """
    Evento pendiente de enviar al cloud.

    Lifecycle: PENDIENTE -> (push exitoso) -> CONFIRMADO
                         -> (error temporal) -> ERROR (intentos++)
                         -> (intentos >= max) -> DESCARTADO (manual review)
    """

    TIPO_CHOICES = TIPOS_EVENTO
       

    ESTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente'),
        ('SIN_PAYLOAD', 'Sin payload (serializar al enviar)'),
        ('CONFIRMADO', 'Confirmado'),
        ('ERROR', 'Error'),
        ('DESCARTADO', 'Descartado'),
    ]

    # Estados que el push debe reclamar de la cola.
    ESTADOS_ENVIABLES = ['PENDIENTE', 'ERROR', 'SIN_PAYLOAD']

    # Identidad del evento
    sucursal = models.ForeignKey(
        'sucursales.Sucursal',
        on_delete=models.PROTECT,
        related_name='eventos_sync',
        verbose_name='Sucursal',
        null=True,
        blank=True,
        help_text='Sucursal origen del evento. Null para eventos legacy.'
    )
    tipo_evento = models.CharField(
        max_length=32,
        choices=TIPO_CHOICES,
        db_index=True,
        verbose_name='Tipo de evento'
    )
    objeto_referencia = models.CharField(
        max_length=64,
        blank=True,
        default='',
        db_index=True,
        verbose_name='Referencia del objeto',
        help_text='Ej: numero_venta. Util para buscar el evento de una venta sin abrir el payload.'
    )
    objeto_id_local = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='ID local del objeto',
        help_text='PK del objeto en la BD local. Util para debug.'
    )

    # Contenido
    #
    # payload/hash son NULOS a proposito cuando el evento se encolo pero la
    # serializacion fallo (estado SIN_PAYLOAD). Preferimos registrar que el
    # hecho ocurrio -- con payload vacio y reintento diferido -- antes que
    # perder el evento o tumbar la venta que lo origino. El push los completa
    # re-serializando desde la BD via apps/sync/registry.py.
    payload = models.JSONField(
        null=True,
        blank=True,
        verbose_name='Payload',
        help_text='Datos serializados que se envian al cloud. Nulo si esta pendiente de serializar.'
    )
    hash_payload = models.CharField(
        max_length=64,
        blank=True,
        default='',
        db_index=True,
        verbose_name='Hash del payload',
        help_text='SHA-256 hex del payload. Permite idempotencia en el cloud.'
    )

    # Estado y auditoria
    estado = models.CharField(
        max_length=16,
        choices=ESTADO_CHOICES,
        default='PENDIENTE',
        db_index=True,
        verbose_name='Estado'
    )
    intentos = models.PositiveIntegerField(
        default=0,
        verbose_name='Intentos de envio'
    )
    ultimo_error = models.TextField(
        blank=True,
        default='',
        verbose_name='Ultimo error'
    )

    # Timestamps
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name='Creado'
    )
    sent_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Enviado'
    )
    confirmed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Confirmado por cloud'
    )

    class Meta:
        verbose_name = 'Evento de sync'
        verbose_name_plural = 'Eventos de sync'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['estado', 'created_at']),
            models.Index(fields=['sucursal', 'estado']),
            models.Index(fields=['tipo_evento', 'estado']),
        ]
        constraints = [
            # Idempotencia con respaldo de BD, no solo de aplicacion.
            #
            # En el cloud, `recibir_eventos` consultaba el hash y DESPUES abria
            # la transaccion del handler: dos requests con el mismo hash podian
            # pasar los dos por el `exists()` y aplicar el pago dos veces. Con
            # esta constraint, el `EventoSync.objects.create()` que corre DENTRO
            # de la misma transaccion que el handler hace de reserva: el segundo
            # INSERT falla, la transaccion revierte y el efecto no se duplica.
            #
            # El hash identifica un hecho, no un envio: todos los payloads
            # llevan una PK local (`pago_id_local`, `movimiento_id_local`, ...)
            # o un timestamp propio, asi que dos hechos distintos no pueden
            # colisionar. Reenviar el mismo hecho SI colisiona, que es
            # exactamente lo que se quiere.
            #
            # Se excluye el hash vacio: los eventos SIN_PAYLOAD todavia no lo
            # tienen y son varios legitimamente.
            models.UniqueConstraint(
                fields=['hash_payload'],
                condition=~models.Q(hash_payload=''),
                name='uniq_eventosync_hash_no_vacio',
            ),
        ]

    def __str__(self):
        ref = self.objeto_referencia or f'#{self.pk}'
        return f'{self.tipo_evento} {ref} [{self.estado}]'

    def marcar_confirmado(self):
        """Marca el evento como confirmado por el cloud."""
        self.estado = 'CONFIRMADO'
        self.confirmed_at = timezone.now()
        if not self.sent_at:
            self.sent_at = self.confirmed_at
        self.save(update_fields=['estado', 'confirmed_at', 'sent_at'])

    def marcar_error(self, mensaje, max_retries=10):
        """
        Marca error; si supera max_retries, pasa a DESCARTADO.

        La transicion es CONDICIONAL: nunca degrada un evento ya CONFIRMADO.
        Dos workers pueden empujar el mismo evento a la vez; si la respuesta
        lenta de uno llega despues de que el otro lo confirmo, aplicar el error
        sobre una instancia obsoleta reabria un evento ya entregado y lo hacia
        rebotar contra el cloud hasta agotar intentos.
        """
        aplicado = type(self).objects.filter(
            pk=self.pk,
            estado__in=self.ESTADOS_ENVIABLES,
        ).update(
            intentos=models.F('intentos') + 1,
            ultimo_error=(mensaje or '')[:2000],
            estado=models.Case(
                models.When(
                    intentos__gte=max_retries - 1,
                    then=models.Value('DESCARTADO'),
                ),
                default=models.Value('ERROR'),
                output_field=models.CharField(),
            ),
        )
        if aplicado:
            self.refresh_from_db(fields=['estado', 'intentos', 'ultimo_error'])
        return bool(aplicado)

    def reactivar(self):
        """
        Devuelve el evento a la cola de envio de forma efectiva.

        Reinicia `intentos`: el push excluye por `intentos >= SYNC_MAX_RETRIES`,
        asi que poner el estado en PENDIENTE sin tocar el contador dejaba el
        evento invisible para el daemon aunque el Admin dijera lo contrario.
        Unica funcion de dominio para reintentar, usada por el Admin y por
        `verificar_sync --reintentar-descartados`.
        """
        self.estado = 'SIN_PAYLOAD' if not self.payload else 'PENDIENTE'
        self.intentos = 0
        self.ultimo_error = ''
        self.sent_at = None
        self.save(update_fields=['estado', 'intentos', 'ultimo_error', 'sent_at'])
        return self


def reactivar_eventos(queryset):
    """
    Devuelve a la cola de envio todos los eventos de `queryset`. Retorna cuantos.

    Unica implementacion de "reintentar", compartida por el Admin y por
    `verificar_sync --reintentar-descartados`. Antes cada uno hacia lo suyo: el
    Admin ponia PENDIENTE sin tocar `intentos`, y como el push excluye por
    `intentos >= SYNC_MAX_RETRIES`, el daemon nunca volvia a mirarlos. El
    operador veia "N eventos puestos en cola" y no pasaba nada.

    El estado destino depende del payload: un evento sin payload vuelve como
    SIN_PAYLOAD para que el push lo re-serialice desde la BD.
    """
    ids = list(queryset.values_list('id', flat=True))
    if not ids:
        return 0

    base = EventoSync.objects.filter(id__in=ids)
    comun = {'intentos': 0, 'ultimo_error': '', 'sent_at': None}

    return (
        base.filter(payload__isnull=True).update(estado='SIN_PAYLOAD', **comun)
        + base.filter(payload__isnull=False).update(estado='PENDIENTE', **comun)
    )


class VersionMaestro(models.Model):
    """
    Cursor de sincronizacion para cada tabla de datos maestros.

    Por cada tabla (productos, categorias, clientes, configuracion) guardamos
    el timestamp del registro mas reciente que se sincronizo correctamente.
    La siguiente corrida solo pide cambios "?desde=<ultima_version>".
    """

    TABLA_CHOICES = [
        ('productos', 'Productos'),
        ('categorias', 'Categorias'),
        ('clientes', 'Clientes'),
        ('configuracion', 'Configuracion'),
        ('metodos_credito', 'Metodos de credito'),
        ('roles', 'Roles'),
        ('asignaciones', 'Asignaciones'),
    ]

    tabla = models.CharField(
        max_length=32,
        choices=TABLA_CHOICES,
        unique=True,
        verbose_name='Tabla'
    )
    ultima_version = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Ultima version descargada',
        help_text='Timestamp del registro mas reciente aplicado localmente.'
    )
    # Mitad `id` del cursor keyset. Junto con `ultima_version` forma la clave
    # (fecha_modificacion, id) que da un orden TOTAL: dos registros guardados en
    # el mismo instante ya no pueden perderse en el borde del cursor.
    ultimo_id = models.PositiveIntegerField(
        default=0,
        verbose_name='Ultimo id aplicado',
        help_text='Desempate del cursor cuando varios registros comparten fecha.'
    )
    # Un cursor que deja de avanzar porque un registro falla al aplicarse. Antes
    # ese registro se saltaba en silencio y se perdia para siempre; ahora el
    # cursor se congela y esto lo hace visible.
    bloqueado_desde = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Bloqueado desde',
        help_text='Cuando el cursor dejo de avanzar por un registro que falla.'
    )
    bloqueado_detalle = models.TextField(
        blank=True,
        default='',
        verbose_name='Detalle del bloqueo',
        help_text='Que registro y que error estan frenando el cursor.'
    )
    ultima_sync_exitosa = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Ultima sync exitosa'
    )
    registros_ultima_sync = models.IntegerField(
        default=0,
        verbose_name='Registros en ultima sync'
    )

    class Meta:
        verbose_name = 'Version de datos maestros'
        verbose_name_plural = 'Versiones de datos maestros'
        ordering = ['tabla']

    def __str__(self):
        ver = self.ultima_version.strftime('%Y-%m-%d %H:%M') if self.ultima_version else 'nunca'
        return f'{self.get_tabla_display()}: {ver}'

    @classmethod
    def get_o_crear(cls, tabla):
        """Helper: garantiza que existe un cursor para la tabla."""
        obj, _ = cls.objects.get_or_create(tabla=tabla)
        return obj

    def marcar_bloqueado(self, detalle):
        """Registra que el cursor no puede avanzar por un registro problematico."""
        campos = ['bloqueado_detalle']
        self.bloqueado_detalle = (detalle or '')[:2000]
        if self.bloqueado_desde is None:
            self.bloqueado_desde = timezone.now()
            campos.append('bloqueado_desde')
        self.save(update_fields=campos)

    def limpiar_bloqueo(self):
        """El cursor volvio a avanzar limpio: se olvida el bloqueo."""
        if self.bloqueado_desde is None and not self.bloqueado_detalle:
            return
        self.bloqueado_desde = None
        self.bloqueado_detalle = ''
        self.save(update_fields=['bloqueado_desde', 'bloqueado_detalle'])


class InventarioMovimientoSync(models.Model):
    """
    Ledger cloud de movimientos de inventario recibidos por sync.

    No intenta reconstruir FIFO cloud: conserva el hecho operativo tal como lo
    emitio la sucursal para auditoria, trazabilidad y diagnostico.
    """

    sucursal = models.ForeignKey(
        'sucursales.Sucursal',
        on_delete=models.PROTECT,
        related_name='movimientos_inventario_sync',
        verbose_name='Sucursal',
    )
    tipo = models.CharField(max_length=32, db_index=True, verbose_name='Tipo')
    movimiento_id_local = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='ID local del movimiento',
    )
    referencia_tipo = models.CharField(max_length=50, blank=True, default='')
    referencia_id = models.PositiveIntegerField(null=True, blank=True)
    producto_sku = models.CharField(max_length=50, db_index=True)
    producto_nombre = models.CharField(max_length=200, blank=True, default='')
    lote_numero = models.CharField(max_length=50, blank=True, default='')
    cantidad = models.IntegerField()
    cantidad_anterior = models.IntegerField(null=True, blank=True)
    cantidad_nueva = models.IntegerField(null=True, blank=True)
    costo_unitario = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    usuario_username = models.CharField(max_length=150, blank=True, default='')
    notas = models.TextField(blank=True, default='')
    fecha_movimiento = models.DateTimeField(db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Movimiento de inventario sincronizado'
        verbose_name_plural = 'Movimientos de inventario sincronizados'
        ordering = ['-fecha_movimiento', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['sucursal', 'movimiento_id_local'],
                name='unique_movimiento_inventario_sync_local',
            ),
        ]
        indexes = [
            models.Index(fields=['sucursal', 'producto_sku']),
            models.Index(fields=['referencia_tipo', 'referencia_id']),
        ]

    def __str__(self):
        return f'{self.sucursal.codigo} {self.producto_sku} {self.tipo} {self.cantidad}'


class InventarioSucursalSnapshot(models.Model):
    """
    Ultimo snapshot de stock por producto y sucursal.

    Es la fuente cloud para inventario multi-sucursal: los eventos dan
    trazabilidad, el snapshot da el estado actual confiable.
    """

    sucursal = models.ForeignKey(
        'sucursales.Sucursal',
        on_delete=models.PROTECT,
        related_name='inventario_snapshots',
        verbose_name='Sucursal',
    )
    producto_sku = models.CharField(max_length=50, db_index=True)
    producto_nombre = models.CharField(max_length=200, blank=True, default='')
    stock_actual = models.IntegerField(default=0)
    stock_minimo = models.IntegerField(default=0)
    bajo_stock = models.BooleanField(default=False, db_index=True)
    valor_fifo = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    timestamp = models.DateTimeField(db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Snapshot de inventario por sucursal'
        verbose_name_plural = 'Snapshots de inventario por sucursal'
        ordering = ['sucursal', 'producto_sku']
        constraints = [
            models.UniqueConstraint(
                fields=['sucursal', 'producto_sku'],
                name='unique_snapshot_inventario_sucursal_producto',
            ),
        ]
        indexes = [
            models.Index(fields=['sucursal', 'bajo_stock']),
            models.Index(fields=['producto_sku', 'timestamp']),
        ]

    def __str__(self):
        return f'{self.sucursal.codigo} {self.producto_sku}: {self.stock_actual}'


class LogSync(models.Model):
    """
    Log de cada corrida del engine de sync.

    Sirve para diagnostico (el admin ve en el dashboard "ultima sync: hace X min")
    y para detectar problemas (muchos FALLO seguidos => algo anda mal).
    """

    TIPO_CHOICES = [
        ('PUSH', 'Push de eventos'),
        ('PULL', 'Pull de maestros'),
        ('PING', 'Verificar conexion'),
        ('FULL', 'Ciclo completo'),
    ]

    RESULTADO_CHOICES = [
        ('EXITOSO', 'Exitoso'),
        ('PARCIAL', 'Parcial (algunos fallos)'),
        ('FALLO', 'Fallo completo'),
    ]

    tipo = models.CharField(
        max_length=16,
        choices=TIPO_CHOICES,
        db_index=True,
        verbose_name='Tipo'
    )
    resultado = models.CharField(
        max_length=16,
        choices=RESULTADO_CHOICES,
        verbose_name='Resultado'
    )
    sucursal = models.ForeignKey(
        'sucursales.Sucursal',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='logs_sync',
        verbose_name='Sucursal'
    )
    inicio = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name='Inicio'
    )
    fin = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Fin'
    )
    duracion_ms = models.IntegerField(
        default=0,
        verbose_name='Duracion (ms)'
    )
    eventos_procesados = models.IntegerField(default=0, verbose_name='Eventos procesados')
    eventos_exitosos = models.IntegerField(default=0, verbose_name='Eventos exitosos')
    eventos_fallidos = models.IntegerField(default=0, verbose_name='Eventos fallidos')
    registros_descargados = models.IntegerField(default=0, verbose_name='Registros descargados')
    mensaje = models.TextField(blank=True, default='', verbose_name='Mensaje')

    class Meta:
        verbose_name = 'Log de sync'
        verbose_name_plural = 'Logs de sync'
        ordering = ['-inicio']
        indexes = [
            models.Index(fields=['-inicio']),
            models.Index(fields=['tipo', '-inicio']),
        ]

    def __str__(self):
        return f'{self.tipo} {self.resultado} {self.inicio:%Y-%m-%d %H:%M:%S}'

    def finalizar(self, resultado, mensaje=''):
        """Cierra el log con el resultado final."""
        self.fin = timezone.now()
        self.duracion_ms = int((self.fin - self.inicio).total_seconds() * 1000)
        self.resultado = resultado
        if mensaje:
            self.mensaje = mensaje[:5000]
        self.save()
