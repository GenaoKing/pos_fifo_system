"""
apps/negocios/models.py
Modelo Negocio: el "tenant" del sistema.

Un Negocio agrupa N Sucursales (ej. Royal Plast con varias tiendas) y es la
unidad a la que se anclan los roles y permisos configurables. Hoy el sistema
corre como instalaciones independientes (una Sucursal por instalacion via
settings.SUCURSAL_CODIGO); este modelo introduce la capa de negocio para que
cada tenant configure sus propios roles.

Forward-compatible con una futura migracion a django-tenants: `slug` puede
mapearse al nombre del schema por tenant.
"""
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class NegocioAmbiguo(RuntimeError):
    """Una base tenant tiene mas de un `Negocio` y no se puede elegir cual es."""


class Negocio(models.Model):
    """Tenant. Agrupa sucursales, usuarios y roles."""

    nombre = models.CharField('Nombre', max_length=200)
    slug = models.SlugField(
        'Slug',
        max_length=120,
        unique=True,
        help_text='Identificador unico del negocio (routing/tenant). '
                  'Se genera del nombre si se deja vacio.',
    )
    rnc = models.CharField(
        'RNC',
        max_length=20,
        blank=True,
        help_text='Registro Nacional del Contribuyente',
    )
    activo = models.BooleanField('Activo', default=True)

    fecha_creacion = models.DateTimeField('Fecha de creacion', default=timezone.now)
    fecha_modificacion = models.DateTimeField('Fecha de modificacion', auto_now=True)

    class Meta:
        verbose_name = 'Negocio'
        verbose_name_plural = 'Negocios'
        ordering = ['nombre']
        db_table = 'negocios'

    def __str__(self):
        return self.nombre

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._slug_unico(self.nombre)
        super().save(*args, **kwargs)

    @classmethod
    def self_row(cls):
        """
        La unica fila `Negocio` de una base tenant (NEG-005).

        Bajo DB-per-tenant el diseno supone exactamente una: la base ES el
        negocio. El esquema, en cambio, admite varias, y tanto `bootstrap_tenant`
        como `normalizar_import_tenant` elegian `order_by('id').first()`. Con dos
        filas, el bootstrap retitulaba la de menor PK mientras usuarios, roles y
        sucursales podian seguir colgando de la otra: el tenant quedaba partido
        en dos sin que nada lo dijera.

        Levanta `NegocioAmbiguo` si hay mas de una, para que el aprovisionamiento
        se detenga y alguien decida como consolidar en vez de que el comando
        elija en silencio.
        """
        filas = list(cls.objects.order_by('id')[:2])
        if len(filas) > 1:
            raise NegocioAmbiguo(
                'La base tenant tiene mas de una fila Negocio. El provisioning '
                'no puede elegir cual es el self-row: consolidar primero.'
            )
        return filas[0] if filas else None

    @classmethod
    def _slug_unico(cls, nombre):
        """Genera un slug unico a partir del nombre, agregando sufijo si choca."""
        base = slugify(nombre)[:110] or 'negocio'
        slug = base
        i = 2
        while cls.objects.filter(slug=slug).exists():
            slug = f'{base}-{i}'
            i += 1
        return slug
