"""
apps/facturacion_electronica/models.py

Modelos de tracking de e-CF. La emisión real la hace una implementación
concreta de `EmisorECFInterface`; estos modelos solo persisten el ciclo
de vida de cada documento para auditoría e historial fiscal.

Diseño:
- `Emisor` representa la entidad fiscal (un RNC). Una sucursal lo
  referencia desde su `ConfiguracionNegocio.emisor_activo`. Múltiples
  Emisor permitidos para preservar atribución histórica si la razón
  social cambia.
- `ECF` es el documento como tal, con FK al Emisor que existía cuando
  se emitió.
- `EventoECF` registra cada transición de estado, manteniendo trazabilidad
  completa para resolución de incidencias.
"""
from django.db import models

from .interfaces import EstadosECF


# =============================================================================
# Choices compartidos
# =============================================================================

PROVEEDOR_CHOICES = (
    ('mseller', 'MSeller (PSFE)'),
    ('nativo', 'Librería nativa (dgii-ecf-py)'),
)

TIPO_ECF_CHOICES = (
    # Estándares
    ('31', 'Factura de Crédito Fiscal Electrónico'),
    ('32', 'Factura de Consumo Electrónica'),
    ('33', 'Nota de Débito Electrónica'),
    ('34', 'Nota de Crédito Electrónica'),
    
    # Especiales y Retenciones
    ('41', 'Comprobante de Compras Electrónico'),
    ('43', 'Comprobante de Gastos Menores Electrónico'),
    ('44', 'Comprobante de Regímenes Especiales Electrónico'),
    ('45', 'Comprobante Gubernamental Electrónico'),
    ('46', 'Comprobante para Exportaciones Electrónico'),
    ('47', 'Comprobante para Pagos al Exterior Electrónico'),
)


# =============================================================================
# Emisor
# =============================================================================

class Emisor(models.Model):
    """
    Entidad fiscal que emite e-CF. Un RNC = un Emisor.

    Diseño multi-Emisor a propósito: si una sucursal cambia de razón
    social a futuro, los ECF históricos retienen FK al Emisor que
    existía cuando se emitieron. La sucursal apunta al activo via
    `ConfiguracionNegocio.emisor_activo`.
    """
    rnc = models.CharField(
        'RNC',
        max_length=11,
        unique=True,
        help_text='Registro Nacional del Contribuyente. 9 u 11 dígitos sin guiones.',
    )
    razon_social = models.CharField('Razón social', max_length=200)
    nombre_comercial = models.CharField('Nombre comercial', max_length=200, blank=True)
    direccion = models.TextField('Dirección', blank=True)

    proveedor_actual = models.CharField(
        'Proveedor actual',
        max_length=20,
        choices=PROVEEDOR_CHOICES,
        default='mseller',
        help_text='Implementación de EmisorECFInterface a usar para este RNC.',
    )
    config_proveedor = models.JSONField(
        'Configuración del proveedor',
        default=dict,
        blank=True,
        help_text=(
            'Datos específicos del proveedor activo. Para credenciales '
            'sensibles (API keys, contraseñas), guardar el NOMBRE de la '
            'variable de entorno, NO el valor. Ejemplo: '
            '{"api_key_env": "MSELLER_API_KEY_ROYAL", '
            '"endpoint": "https://ecf.mseller.app/api"}'
        ),
    )

    activo = models.BooleanField('Activo', default=True)

    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Emisor e-CF'
        verbose_name_plural = 'Emisores e-CF'
        ordering = ['-activo', 'razon_social']

    def __str__(self):
        return f'{self.razon_social} (RNC {self.rnc})'


# =============================================================================
# ECF
# =============================================================================

class ECF(models.Model):
    """
    Comprobante fiscal electrónico. Un registro por documento emitido,
    independiente de si terminó aprobado, rechazado o en error técnico.
    """
    emisor = models.ForeignKey(
        Emisor,
        on_delete=models.PROTECT,
        related_name='ecfs',
        verbose_name='Emisor',
    )
    venta = models.ForeignKey(
        'ventas.Venta',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name='Venta',
        help_text='Venta de origen. Null para tipo 34 que no proviene de anulación directa.',
    )

    tipo = models.CharField('Tipo', max_length=2, choices=TIPO_ECF_CHOICES)
    encf = models.CharField(
        'eNCF',
        max_length=13,
        blank=True,
        db_index=True,
        help_text='Formato E + tipo + 10 dígitos. Asignado por el proveedor al emitir.',
    )

    fecha_emision = models.DateTimeField('Fecha de emisión', auto_now_add=True)

    estado = models.CharField(
        'Estado',
        max_length=25,
        choices=EstadosECF.CHOICES,
        default=EstadosECF.PENDIENTE,
        db_index=True,
    )

    track_id = models.CharField(
        'Track ID',
        max_length=100,
        blank=True,
        db_index=True,
        help_text='Identificador asignado por el proveedor para seguimiento.',
    )
    codigo_seguridad = models.CharField(
        'Código de seguridad',
        max_length=20,
        blank=True,
        help_text='Asignado por DGII al aprobar. Se usa en el QR del ticket impreso.',
    )

    proveedor_usado = models.CharField(
        'Proveedor usado',
        max_length=20,
        choices=PROVEEDOR_CHOICES,
        help_text='Snapshot al momento de emitir. Permite trazabilidad si el emisor cambia de proveedor a futuro.',
    )

    xml_firmado = models.TextField(
        'XML firmado',
        blank=True,
        help_text='Copia local del XML enviado a DGII. Protege ante cambio futuro de PSFE.',
    )
    xml_respuesta = models.TextField(
        'XML respuesta DGII',
        blank=True,
        help_text='Respuesta cruda recibida de DGII (vía proveedor).',
    )

    intentos = models.PositiveSmallIntegerField('Intentos', default=0)

    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'e-CF'
        verbose_name_plural = 'e-CFs'
        ordering = ['-creado_en']
        indexes = [
            models.Index(fields=['estado', 'creado_en']),
            models.Index(fields=['emisor', 'tipo']),
        ]
        constraints = [
            # eNCF único por emisor cuando ya fue asignado (excluye los vacíos)
            models.UniqueConstraint(
                fields=['emisor', 'encf'],
                condition=~models.Q(encf=''),
                name='unique_encf_por_emisor',
            ),
        ]

    def __str__(self):
        return f'{self.encf or "(sin eNCF)"} — {self.get_estado_display()}'

    def es_terminal(self):
        return self.estado in EstadosECF.TERMINALES

    def es_reintentable(self):
        return self.estado in EstadosECF.REINTENTABLES and self.intentos < 5


# =============================================================================
# EventoECF
# =============================================================================

class EventoECF(models.Model):
    """
    Bitácora de transiciones de estado. Cada cambio en `ECF.estado` debe
    generar un EventoECF, idealmente en la misma transacción que el cambio.
    """
    ecf = models.ForeignKey(
        ECF,
        on_delete=models.CASCADE,
        related_name='eventos',
        verbose_name='e-CF',
    )
    fecha = models.DateTimeField(auto_now_add=True, db_index=True)

    estado_anterior = models.CharField(
        'Estado anterior',
        max_length=25,
        blank=True,
        help_text='Vacío si es el evento inicial de creación.',
    )
    estado_nuevo = models.CharField('Estado nuevo', max_length=25)

    mensaje = models.TextField('Mensaje', blank=True)
    payload = models.JSONField(
        'Payload',
        default=dict,
        blank=True,
        help_text='Datos asociados al evento (respuesta del proveedor, error, contexto).',
    )

    class Meta:
        verbose_name = 'Evento e-CF'
        verbose_name_plural = 'Eventos e-CF'
        ordering = ['-fecha']

    def __str__(self):
        anterior = self.estado_anterior or '—'
        return f'{self.ecf.encf or "(sin eNCF)"}: {anterior} → {self.estado_nuevo}'