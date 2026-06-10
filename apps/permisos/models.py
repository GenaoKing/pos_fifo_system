"""
apps/permisos/models.py
Motor RBAC data-driven, multitenant.

Modelo:
    Permiso        -> catalogo GLOBAL de acciones gateables (system-wide).
    Rol            -> rol por Negocio (tenant). El "Cajero" de un negocio
                      es independiente del de otro.
    Rol.permisos   -> mapa rol->permiso configurable en runtime por el admin.
    AsignacionRol  -> asigna un Rol a un Usuario, opcionalmente acotado a una
                      Sucursal (null = todas las sucursales del negocio).

El enforcement se hace via apps/permisos/engine.py (cacheado).
"""
from django.conf import settings
from django.db import models


class Permiso(models.Model):
    """Catalogo global de acciones de negocio. Sembrado desde catalogo.py."""

    codigo = models.CharField(
        'Codigo',
        max_length=100,
        unique=True,
        help_text="Identificador de la accion. Ej: 'clientes.crear'.",
    )
    nombre = models.CharField('Nombre', max_length=150)
    descripcion = models.TextField('Descripcion', blank=True)
    modulo = models.CharField(
        'Modulo',
        max_length=50,
        db_index=True,
        help_text='Agrupacion para la UI. Ej: clientes, ventas.',
    )

    fecha_creacion = models.DateTimeField('Fecha de creacion', auto_now_add=True)

    class Meta:
        verbose_name = 'Permiso'
        verbose_name_plural = 'Permisos'
        ordering = ['modulo', 'codigo']
        db_table = 'permisos'

    def __str__(self):
        return self.codigo


class Rol(models.Model):
    """Rol configurable, scoped a un Negocio (tenant)."""

    negocio = models.ForeignKey(
        'negocios.Negocio',
        on_delete=models.CASCADE,
        related_name='roles',
        verbose_name='Negocio',
    )
    nombre = models.CharField('Nombre', max_length=100)
    slug = models.SlugField(
        'Slug',
        max_length=100,
        help_text='Identificador del rol dentro del negocio. Ej: cajero, admin.',
    )
    descripcion = models.TextField('Descripcion', blank=True)
    es_sistema = models.BooleanField(
        'Es de sistema',
        default=False,
        help_text='Roles por defecto creados en el seed. Se protegen de borrado.',
    )
    activo = models.BooleanField('Activo', default=True)

    permisos = models.ManyToManyField(
        Permiso,
        related_name='roles',
        blank=True,
        verbose_name='Permisos',
    )

    fecha_creacion = models.DateTimeField('Fecha de creacion', auto_now_add=True)
    fecha_modificacion = models.DateTimeField('Fecha de modificacion', auto_now=True)

    class Meta:
        verbose_name = 'Rol'
        verbose_name_plural = 'Roles'
        ordering = ['negocio', 'nombre']
        db_table = 'roles'
        unique_together = ('negocio', 'slug')

    def __str__(self):
        return f'{self.nombre} ({self.negocio.nombre})'


class AsignacionRol(models.Model):
    """Asigna un Rol a un Usuario, opcionalmente acotado a una Sucursal."""

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='asignaciones_rol',
        verbose_name='Usuario',
    )
    rol = models.ForeignKey(
        Rol,
        on_delete=models.CASCADE,
        related_name='asignaciones',
        verbose_name='Rol',
    )
    sucursal = models.ForeignKey(
        'sucursales.Sucursal',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='asignaciones_rol',
        verbose_name='Sucursal',
        help_text='Null = aplica en todas las sucursales del negocio.',
    )
    activo = models.BooleanField('Activo', default=True)
    fecha_creacion = models.DateTimeField('Fecha de creacion', auto_now_add=True)
    fecha_modificacion = models.DateTimeField('Fecha de modificacion', auto_now=True)

    class Meta:
        verbose_name = 'Asignacion de rol'
        verbose_name_plural = 'Asignaciones de rol'
        db_table = 'asignaciones_rol'
        unique_together = ('usuario', 'rol', 'sucursal')

    def __str__(self):
        return f'{self.usuario} -> {self.rol}'
