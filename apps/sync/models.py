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
        ('CONFIRMADO', 'Confirmado'),
        ('ERROR', 'Error'),
        ('DESCARTADO', 'Descartado'),
    ]

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
    payload = models.JSONField(
        verbose_name='Payload',
        help_text='Datos serializados que se envian al cloud.'
    )
    hash_payload = models.CharField(
        max_length=64,
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
        """Marca error; si supera max_retries, pasa a DESCARTADO."""
        self.intentos += 1
        self.ultimo_error = (mensaje or '')[:2000]
        if self.intentos >= max_retries:
            self.estado = 'DESCARTADO'
        else:
            self.estado = 'ERROR'
        self.save(update_fields=['estado', 'intentos', 'ultimo_error'])


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