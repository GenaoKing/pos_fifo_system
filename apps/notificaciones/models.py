from django.conf import settings
from django.db import models
from django.utils import timezone


class MotorNotificaciones(models.Model):
    """Interruptor por tenant y corte que impide proyectar eventos historicos."""

    clave = models.CharField(max_length=20, primary_key=True, default='default')
    activo = models.BooleanField(default=False)
    activado_desde = models.DateTimeField(null=True, blank=True)
    ultima_purga = models.DateTimeField(null=True, blank=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'notificaciones_motor'

    @classmethod
    def actual(cls):
        obj, _ = cls.objects.get_or_create(clave='default')
        return obj


class ReglaNotificacionRol(models.Model):
    rol = models.ForeignKey(
        'permisos.Rol', on_delete=models.CASCADE,
        related_name='reglas_notificacion',
    )
    tipo_evento = models.CharField(max_length=64, db_index=True)
    activa = models.BooleanField(default=True)
    enviar_push = models.BooleanField(default=True)
    parametros = models.JSONField(default=dict, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'notificaciones_reglas_rol'
        ordering = ['tipo_evento', 'rol__nombre']
        constraints = [
            models.UniqueConstraint(
                fields=['rol', 'tipo_evento'], name='notif_regla_rol_tipo_unica',
            ),
        ]


class ExcepcionNotificacionUsuario(models.Model):
    INCLUIR = 'INCLUIR'
    EXCLUIR = 'EXCLUIR'
    MODOS = [(INCLUIR, 'Incluir'), (EXCLUIR, 'Excluir')]

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='excepciones_notificacion',
    )
    tipo_evento = models.CharField(max_length=64, db_index=True)
    modo = models.CharField(max_length=10, choices=MODOS)
    enviar_push = models.BooleanField(default=True)
    parametros = models.JSONField(default=dict, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'notificaciones_excepciones_usuario'
        ordering = ['tipo_evento', 'usuario__username']
        constraints = [
            models.UniqueConstraint(
                fields=['usuario', 'tipo_evento'],
                name='notif_excepcion_usuario_tipo_unica',
            ),
        ]


class EventoNotificable(models.Model):
    """Hecho inmutable del que se deriva la bandeja de uno o mas usuarios."""

    tipo_evento = models.CharField(max_length=64, db_index=True)
    fuente = models.CharField(max_length=32, default='sync')
    clave_fuente = models.CharField(max_length=128)
    sucursal = models.ForeignKey(
        'sucursales.Sucursal', on_delete=models.PROTECT,
        null=True, blank=True, related_name='eventos_notificables',
    )
    titulo = models.CharField(max_length=180)
    cuerpo = models.CharField(max_length=500)
    datos = models.JSONField(default=dict, blank=True)
    ruta = models.CharField(max_length=255, blank=True, default='')
    ocurrido_en = models.DateTimeField(db_index=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'notificaciones_eventos'
        ordering = ['-ocurrido_en', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['fuente', 'clave_fuente', 'tipo_evento'],
                name='notif_evento_fuente_unica',
            ),
        ]
        indexes = [
            models.Index(fields=['tipo_evento', '-ocurrido_en'], name='notif_evento_tipo_fecha'),
        ]


class EventoSyncNotificacionProcesado(models.Model):
    """Tombstone durable: evita reproyectar un EventoSync aun tras la purga.

    Tambien acota los reintentos de proyeccion: un payload que revienta
    `construir_desde_sync` no puede reintentarse cada minuto para siempre.
    """

    PROCESADO = 'PROCESADO'
    REINTENTO = 'REINTENTO'
    FALLIDO = 'FALLIDO'
    ESTADOS = [
        (PROCESADO, 'Procesado'),
        (REINTENTO, 'En reintento'),
        (FALLIDO, 'Fallido'),
    ]

    evento_sync = models.OneToOneField(
        'sync.EventoSync', on_delete=models.CASCADE,
        related_name='proyeccion_notificacion',
    )
    procesado_en = models.DateTimeField(default=timezone.now)
    genero_evento = models.BooleanField(default=False)
    estado = models.CharField(
        max_length=10, choices=ESTADOS, default=PROCESADO, db_index=True,
    )
    intentos = models.PositiveSmallIntegerField(default=0)
    proximo_intento_en = models.DateTimeField(null=True, blank=True)
    ultimo_error = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        db_table = 'notificaciones_sync_procesados'


class DestinatarioNotificacion(models.Model):
    NORMAL = 'NORMAL'
    ALERTA = 'ALERTA'
    NIVELES = [(NORMAL, 'Normal'), (ALERTA, 'Alerta')]

    evento = models.ForeignKey(
        EventoNotificable, on_delete=models.CASCADE, related_name='destinatarios',
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='notificaciones_recibidas',
    )
    nivel = models.CharField(max_length=10, choices=NIVELES, default=NORMAL)
    push_habilitado = models.BooleanField(default=True)
    leida_en = models.DateTimeField(null=True, blank=True, db_index=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'notificaciones_destinatarios'
        ordering = ['-evento__ocurrido_en', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['evento', 'usuario'], name='notif_destinatario_unico',
            ),
        ]
        indexes = [
            models.Index(fields=['usuario', 'leida_en'], name='notif_usuario_leida'),
        ]

    def marcar_leida(self):
        if self.leida_en is None:
            self.leida_en = timezone.now()
            self.save(update_fields=['leida_en'])


class SuscripcionPush(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='suscripciones_push',
    )
    endpoint = models.TextField(unique=True)
    p256dh = models.TextField()
    auth = models.TextField()
    nombre_dispositivo = models.CharField(max_length=100, blank=True, default='')
    user_agent = models.CharField(max_length=300, blank=True, default='')
    activa = models.BooleanField(default=True, db_index=True)
    ultimo_exito_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'notificaciones_suscripciones_push'
        ordering = ['-actualizado_en']


class EntregaPush(models.Model):
    PENDIENTE = 'PENDIENTE'
    EN_PROCESO = 'EN_PROCESO'
    ENVIADA = 'ENVIADA'
    FALLIDA = 'FALLIDA'
    DESCARTADA = 'DESCARTADA'
    ESTADOS = [
        (PENDIENTE, 'Pendiente'), (EN_PROCESO, 'En proceso'),
        (ENVIADA, 'Enviada'), (FALLIDA, 'Fallida'),
        (DESCARTADA, 'Descartada'),
    ]

    destinatario = models.ForeignKey(
        DestinatarioNotificacion, on_delete=models.CASCADE,
        related_name='entregas_push',
    )
    suscripcion = models.ForeignKey(
        SuscripcionPush, on_delete=models.CASCADE, related_name='entregas',
    )
    estado = models.CharField(
        max_length=12, choices=ESTADOS, default=PENDIENTE, db_index=True,
    )
    intentos = models.PositiveSmallIntegerField(default=0)
    proximo_intento_en = models.DateTimeField(default=timezone.now, db_index=True)
    lease_hasta = models.DateTimeField(null=True, blank=True, db_index=True)
    ultimo_error = models.CharField(max_length=500, blank=True, default='')
    enviada_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'notificaciones_entregas_push'
        ordering = ['proximo_intento_en', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['destinatario', 'suscripcion'],
                name='notif_entrega_dest_sus_unica',
            ),
        ]
        indexes = [
            models.Index(
                fields=['estado', 'proximo_intento_en'], name='notif_entrega_estado_fecha',
            ),
        ]
