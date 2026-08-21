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
import hashlib
import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


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


class AutorizacionOverride(models.Model):
    """
    Autorizacion puntual de un ADMIN para una operacion que excede una regla.

    Reemplaza el patron anterior: el POS pedia credenciales de admin a un
    endpoint, recibia su `admin_id` crudo y lo REENVIABA junto con la venta.
    Ese ID no probaba nada — cualquiera que lo conociera (o lo adivinara, son
    enteros secuenciales) podia atribuirle una excepcion a un administrador que
    nunca la aprobo, y la auditoria registraba su nombre como si lo hubiera
    hecho.

    Esta fila es una prueba real de aprobacion:

    - **De un solo uso**: se consume atomicamente bajo lock.
    - **De vida corta**: vence en minutos, no dura toda la sesion.
    - **Ligada**: a la operacion, al operador que la pidio, a la sucursal, al
      monto maximo y al alcance (ej. el cliente concreto). Un token emitido
      para otro monto, otro cliente u otra operacion no sirve.
    - **Con motivo obligatorio**: sin motivo no hay autorizacion.

    Del token solo se guarda el SHA-256, igual que `SyncToken`.
    """

    # Operaciones que admiten override. Se declaran aca para que el consumidor
    # no pueda inventar una y saltarse la validacion por un typo.
    OP_CREDITO_EXCEDER_LIMITE = 'credito.exceder_limite'
    OP_CAJA_RETIRO = 'caja.retiro'
    OPERACIONES = [
        (OP_CREDITO_EXCEDER_LIMITE, 'Exceder limite de credito'),
        (OP_CAJA_RETIRO, 'Retiro de caja'),
    ]

    VIGENCIA_MINUTOS = 5

    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    operacion = models.CharField(max_length=64, choices=OPERACIONES, db_index=True)

    autorizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='overrides_autorizados',
        verbose_name='Autorizado por',
    )
    solicitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='overrides_solicitados',
        null=True,
        blank=True,
        verbose_name='Solicitado por',
    )
    sucursal = models.ForeignKey(
        'sucursales.Sucursal',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='overrides',
    )

    monto_maximo = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text='Monto maximo que esta autorizacion cubre.',
    )
    alcance = models.JSONField(
        default=dict, blank=True,
        help_text='Binding adicional, ej. {"cliente_id": 5}. El consumidor '
                  'exige que coincida.',
    )
    motivo = models.CharField(max_length=300)

    creado = models.DateTimeField(default=timezone.now)
    expira = models.DateTimeField(db_index=True)
    consumido_en = models.DateTimeField(null=True, blank=True)
    consumido_referencia = models.CharField(
        max_length=64, blank=True, default='',
        help_text='Que operacion la consumio (ej. numero_venta).',
    )

    class Meta:
        verbose_name = 'Autorizacion de override'
        verbose_name_plural = 'Autorizaciones de override'
        db_table = 'permisos_autorizaciones_override'
        ordering = ['-creado']

    def __str__(self):
        return f'{self.operacion} por {self.autorizado_por} ({self.creado:%Y-%m-%d %H:%M})'

    @staticmethod
    def hash_token(token):
        return hashlib.sha256(str(token).encode('utf-8')).hexdigest()

    @property
    def vencida(self):
        return timezone.now() > self.expira

    @property
    def consumida(self):
        return self.consumido_en is not None

    @classmethod
    def emitir(cls, *, operacion, autorizado_por, motivo, solicitado_por=None,
               sucursal=None, monto_maximo=None, alcance=None,
               minutos=None):
        """
        Crea la autorizacion y devuelve `(instancia, token_plano)`.

        El token plano se devuelve UNA vez: no queda almacenado.
        """
        motivo = (motivo or '').strip()
        if not motivo:
            raise ValueError('Una autorizacion de override requiere motivo.')

        token = secrets.token_urlsafe(32)
        vigencia = minutos or cls.VIGENCIA_MINUTOS
        instancia = cls.objects.create(
            token_hash=cls.hash_token(token),
            operacion=operacion,
            autorizado_por=autorizado_por,
            solicitado_por=solicitado_por,
            sucursal=sucursal,
            monto_maximo=monto_maximo,
            alcance=alcance or {},
            motivo=motivo[:300],
            expira=timezone.now() + timedelta(minutes=vigencia),
        )
        return instancia, token

    @classmethod
    def consumir(cls, *, token, operacion, solicitado_por=None, monto=None,
                 alcance=None, referencia=''):
        """
        Valida y consume la autorizacion. Devuelve la instancia consumida.

        Levanta `AutorizacionInvalida` si el token no existe, ya se uso, vencio,
        es de otra operacion, de otro operador, de otro alcance o cubre menos
        monto del solicitado.

        El consumo va bajo `select_for_update` para que dos ventas simultaneas
        no puedan usar la misma autorizacion.
        """
        if not token:
            raise AutorizacionInvalida('Falta la autorizacion del administrador.')

        try:
            instancia = (
                cls.objects.select_for_update()
                .get(token_hash=cls.hash_token(token), operacion=operacion)
            )
        except cls.DoesNotExist:
            raise AutorizacionInvalida('Autorizacion inexistente o de otra operacion.')

        if instancia.consumida:
            raise AutorizacionInvalida('Esta autorizacion ya fue utilizada.')
        if instancia.vencida:
            raise AutorizacionInvalida('La autorizacion vencio; pedila de nuevo.')

        if solicitado_por is not None and instancia.solicitado_por_id not in (
            None, getattr(solicitado_por, 'pk', None),
        ):
            raise AutorizacionInvalida(
                'La autorizacion fue emitida para otro operador.'
            )

        if monto is not None and instancia.monto_maximo is not None:
            if Decimal(str(monto)) > instancia.monto_maximo:
                raise AutorizacionInvalida(
                    f'La autorizacion cubre hasta ${instancia.monto_maximo} y la '
                    f'operacion es por ${monto}.'
                )

        for clave, valor in (alcance or {}).items():
            esperado = instancia.alcance.get(clave)
            if esperado is not None and str(esperado) != str(valor):
                raise AutorizacionInvalida(
                    f'La autorizacion no aplica a este {clave}.'
                )

        instancia.consumido_en = timezone.now()
        instancia.consumido_referencia = str(referencia or '')[:64]
        instancia.save(update_fields=['consumido_en', 'consumido_referencia'])
        return instancia


class AutorizacionInvalida(Exception):
    """El token de override no existe, ya se uso, vencio o no aplica."""
