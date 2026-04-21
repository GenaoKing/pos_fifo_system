"""
apps/sucursales/models.py
Modelo de sucursal para soporte multi-punto-de-venta.

Cada instalacion del POS se identifica con un codigo de sucursal
definido en settings.SUCURSAL_CODIGO. Esto permite:
- Prefijos unicos en numeros de venta (SD-001-V20260414-0001)
- ConfiguracionNegocio por sucursal
- Trazabilidad de ventas, cierres y auditoria por sucursal
- Futuro: sincronizacion con cloud (Fase 4)
"""
import secrets
from django.db import models
from django.conf import settings


class Sucursal(models.Model):
    """
    Punto de venta fisico.
    Cada PC corriendo el POS apunta a una sucursal via settings.SUCURSAL_CODIGO.
    """
    codigo = models.CharField(
        max_length=20,
        unique=True,
        verbose_name='Codigo',
        help_text='Codigo unico de sucursal. Ej: SD-001, STI-001'
    )

    nombre = models.CharField(
        max_length=200,
        verbose_name='Nombre',
        help_text='Nombre descriptivo. Ej: Royal Plast - Sede Principal'
    )

    direccion = models.TextField(
        blank=True,
        verbose_name='Direccion'
    )

    telefono = models.CharField(
        max_length=50,
        blank=True,
        verbose_name='Telefono'
    )

    activa = models.BooleanField(
        default=True,
        verbose_name='Activa',
        help_text='Sucursales inactivas no pueden operar el POS'
    )

    api_key = models.CharField(
        max_length=64,
        unique=True,
        blank=True,
        verbose_name='API Key',
        help_text='Clave para autenticacion en futuro sistema de sync'
    )

    fecha_creacion = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha de Creacion'
    )

    fecha_modificacion = models.DateTimeField(
        auto_now=True,
        verbose_name='Ultima Modificacion'
    )

    class Meta:
        verbose_name = 'Sucursal'
        verbose_name_plural = 'Sucursales'
        ordering = ['codigo']

    def __str__(self):
        return f'{self.codigo} - {self.nombre}'

    def save(self, *args, **kwargs):
        # Auto-generar api_key si no tiene
        if not self.api_key:
            self.api_key = secrets.token_hex(32)
        super().save(*args, **kwargs)


def get_sucursal_actual():
    """
    Retorna la instancia de Sucursal correspondiente a esta instalacion.
    Lee settings.SUCURSAL_CODIGO para identificar cual es.

    Uso:
        from apps.sucursales.models import get_sucursal_actual
        sucursal = get_sucursal_actual()

    Returns:
        Sucursal | None: La sucursal actual o None si no esta configurada
    """
    from django.core.cache import cache

    codigo = getattr(settings, 'SUCURSAL_CODIGO', None)
    if not codigo:
        return None

    cache_key = f'sucursal_actual_{codigo}'
    sucursal = cache.get(cache_key)
    if sucursal is None:
        try:
            sucursal = Sucursal.objects.get(codigo=codigo)
            cache.set(cache_key, sucursal, timeout=None)
        except Sucursal.DoesNotExist:
            return None
    return sucursal