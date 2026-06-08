"""
apps/suscripciones/models.py
Entitlements de modulos por negocio (tenant) + planes.

Resolucion del set efectivo de modulos:
    cierre( plan.modulos ∪ {NegocioModulo incluido} − {NegocioModulo excluido} ) ∪ core
    − {SucursalModuloOverride apagados}

Ver apps/suscripciones/engine.py. El grafo de dependencias vive en registry.py.
"""
from django.db import models


class Modulo(models.Model):
    """Espejo en DB del registro (registry.py). Para M2M de planes y admin/labels."""

    key = models.CharField('Key', max_length=50, unique=True)
    nombre = models.CharField('Nombre', max_length=150)
    descripcion = models.TextField('Descripcion', blank=True)
    core = models.BooleanField(
        'Es core', default=False,
        help_text='Core = siempre activo, no vendible.',
    )

    class Meta:
        verbose_name = 'Modulo'
        verbose_name_plural = 'Modulos'
        ordering = ['key']
        db_table = 'modulos'

    def __str__(self):
        return self.key


class Plan(models.Model):
    """Tier comercial: un preset de modulos (Basico / Pro / Empresarial)."""

    nombre = models.CharField('Nombre', max_length=100)
    slug = models.SlugField('Slug', max_length=100, unique=True)
    descripcion = models.TextField('Descripcion', blank=True)
    activo = models.BooleanField('Activo', default=True)
    modulos = models.ManyToManyField(
        Modulo, related_name='planes', blank=True, verbose_name='Modulos incluidos',
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Plan'
        verbose_name_plural = 'Planes'
        ordering = ['nombre']
        db_table = 'planes'

    def __str__(self):
        return self.nombre


class SuscripcionNegocio(models.Model):
    """La suscripcion del tenant: su plan base."""

    negocio = models.OneToOneField(
        'negocios.Negocio', on_delete=models.CASCADE, related_name='suscripcion',
        verbose_name='Negocio',
    )
    plan = models.ForeignKey(
        Plan, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='suscripciones', verbose_name='Plan',
    )
    activa = models.BooleanField('Activa', default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Suscripcion de negocio'
        verbose_name_plural = 'Suscripciones de negocio'
        db_table = 'suscripciones_negocio'

    def __str__(self):
        return f'{self.negocio} -> {self.plan or "custom"}'


class NegocioModulo(models.Model):
    """Override a la carte del tenant: suma (`incluido=True`) o quita
    (`incluido=False`) un modulo respecto del plan."""

    negocio = models.ForeignKey(
        'negocios.Negocio', on_delete=models.CASCADE, related_name='modulos_override',
        verbose_name='Negocio',
    )
    modulo = models.ForeignKey(
        Modulo, on_delete=models.CASCADE, related_name='negocio_overrides',
        verbose_name='Modulo',
    )
    incluido = models.BooleanField(
        'Incluido', default=True,
        help_text='True = agregar sobre el plan; False = quitar del plan.',
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Override de modulo (negocio)'
        verbose_name_plural = 'Overrides de modulo (negocio)'
        db_table = 'negocio_modulos'
        unique_together = ('negocio', 'modulo')

    def __str__(self):
        signo = '+' if self.incluido else '-'
        return f'{self.negocio} {signo}{self.modulo.key}'


class SucursalModuloOverride(models.Model):
    """Apaga localmente en una sucursal un modulo que el tenant si tiene
    (la sucursal solo puede apagar, no encender)."""

    sucursal = models.ForeignKey(
        'sucursales.Sucursal', on_delete=models.CASCADE, related_name='modulos_override',
        verbose_name='Sucursal',
    )
    modulo = models.ForeignKey(
        Modulo, on_delete=models.CASCADE, related_name='sucursal_overrides',
        verbose_name='Modulo',
    )
    activo = models.BooleanField(
        'Activo', default=False,
        help_text='False = apagado en esta sucursal.',
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Override de modulo (sucursal)'
        verbose_name_plural = 'Overrides de modulo (sucursal)'
        db_table = 'sucursal_modulo_overrides'
        unique_together = ('sucursal', 'modulo')

    def __str__(self):
        return f'{self.sucursal} apaga {self.modulo.key}'
