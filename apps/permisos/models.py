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
        constraints = [
            # `unique_together` no protegia la asignacion GLOBAL (PER-008):
            # en PostgreSQL y SQLite dos NULL no colisionan, asi que podian
            # coexistir dos filas identicas con `sucursal=NULL`. Revocar una
            # devolvia 204 y el usuario conservaba el permiso por la otra; y
            # `update_or_create` del pull podia levantar
            # `MultipleObjectsReturned` y congelar el cursor de sync.
            #
            # Dos indices parciales cubren los dos casos por separado.
            models.UniqueConstraint(
                fields=['usuario', 'rol'],
                condition=models.Q(sucursal__isnull=True),
                name='asignacion_unica_global',
            ),
            models.UniqueConstraint(
                fields=['usuario', 'rol', 'sucursal'],
                condition=models.Q(sucursal__isnull=False),
                name='asignacion_unica_por_sucursal',
            ),
        ]

    def __str__(self):
        return f'{self.usuario} -> {self.rol}'

    def clean(self):
        """
        Usuario, rol y sucursal tienen que ser del mismo negocio (PER-004).

        El modelo declaraba tres FK independientes y ninguna relacion entre sus
        negocios. `full_clean()` aceptaba una asignacion con usuario del negocio
        B y rol del A, y el motor la convertia en privilegio efectivo — se
        reprodujo en la auditoria. El motor ahora tambien filtra por negocio,
        pero esa es la ultima linea: la fila no deberia poder existir.
        """
        from django.core.exceptions import ValidationError

        errores = {}
        negocio_rol = getattr(self.rol, 'negocio_id', None)

        negocio_usuario = getattr(self.usuario, 'negocio_id', None)
        if negocio_rol and negocio_usuario and negocio_usuario != negocio_rol:
            errores['usuario'] = (
                'El usuario pertenece a otro negocio que el rol asignado.'
            )
        elif negocio_rol and not negocio_usuario:
            errores['usuario'] = (
                'El usuario no tiene negocio: vincularlo antes de asignarle un '
                'rol de tenant.'
            )

        if self.sucursal_id is not None:
            negocio_sucursal = getattr(self.sucursal, 'negocio_id', None)
            if negocio_rol and negocio_sucursal and negocio_sucursal != negocio_rol:
                errores['sucursal'] = (
                    'La sucursal pertenece a otro negocio que el rol asignado.'
                )

        if errores:
            raise ValidationError(errores)


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
    OP_VENTA_DESCUENTO = 'ventas.descuento'
    OPERACIONES = [
        (OP_CREDITO_EXCEDER_LIMITE, 'Exceder limite de credito'),
        (OP_CAJA_RETIRO, 'Retiro de caja'),
        (OP_VENTA_DESCUENTO, 'Aplicar descuento en venta'),
    ]

    # Operaciones donde el motivo puede quedar vacio si la configuracion del
    # negocio asi lo define.
    #
    # El motivo es obligatorio por diseno: sin el, la traza dice QUIEN aprobo
    # pero no POR QUE. Se afloja SOLO para descuentos, y solo porque en un
    # negocio donde se regatea casi toda venta termina con descuento: exigir
    # texto libre en cada una produce 400 filas que dicen "descuento", que es
    # peor que no pedirlo — da la ilusion de control sin aportar informacion.
    # `caja.retiro` y `credito.exceder_limite` son excepciones puntuales y
    # conservan el motivo obligatorio.
    OPERACIONES_MOTIVO_OPCIONAL = {OP_VENTA_DESCUENTO}

    # Permiso que debe tener quien AUTORIZA cada operacion. Vive aca, junto a
    # la declaracion de la operacion, para que agregar una operacion sin decidir
    # quien puede aprobarla sea imposible de pasar por alto.
    PERMISO_REQUERIDO = {
        OP_CREDITO_EXCEDER_LIMITE: 'cuentas_por_cobrar.autorizar_exceso_credito',
        OP_CAJA_RETIRO: 'caja.administrar',
        OP_VENTA_DESCUENTO: 'ventas.autorizar_descuento',
    }

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
    motivo = models.CharField(max_length=300, blank=True, default='')

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
        if not motivo and operacion not in cls.OPERACIONES_MOTIVO_OPCIONAL:
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


class CredencialFisica(models.Model):
    """
    Credencial fisica (carnet, tarjeta, llavero) que identifica a un usuario
    ante el lector de codigo de barras del POS.

    Sirve para autorizar en el mostrador sin teclear usuario y contrasena
    delante del cliente y de la cola. Es una forma alternativa de credencial
    para emitir un `AutorizacionOverride`, no un metodo de login: pasar el
    carnet NO abre sesion ni cambia el usuario del turno.

    Del codigo solo se guarda el SHA-256, igual que `AutorizacionOverride` y
    `SyncToken`. Un dump de la BD no permite fabricar carnets.

    LIMITE CONOCIDO: es una credencial *portadora* — quien la tiene, autoriza.
    Un codigo de barras 1D se copia con una foto y una impresora, y una tarjeta
    se puede prestar o dejar en la gaveta. El control real no es la tarjeta:
    es que cada uso queda nominalmente registrado (quien autorizo, cuanto y
    cuando) y que ese registro viaja al portal. Si hace falta un factor mas
    fuerte, la credencial se combina con contrasena (el endpoint acepta ambas
    formas).
    """

    LONGITUD_MINIMA = 6

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='credenciales_fisicas',
        verbose_name='Usuario',
    )
    codigo_hash = models.CharField(max_length=64, unique=True, db_index=True)
    etiqueta = models.CharField(
        'Etiqueta',
        max_length=60,
        blank=True,
        default='',
        help_text='Como reconocerla fisicamente. Ej: "Carnet supervisor - Ana".',
    )
    activa = models.BooleanField('Activa', default=True)
    fecha_alta = models.DateTimeField('Fecha de alta', default=timezone.now)
    fecha_baja = models.DateTimeField('Fecha de baja', null=True, blank=True)
    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='credenciales_emitidas',
        verbose_name='Dada de alta por',
    )

    class Meta:
        verbose_name = 'Credencial fisica'
        verbose_name_plural = 'Credenciales fisicas'
        db_table = 'permisos_credenciales_fisicas'
        ordering = ['usuario', '-fecha_alta']

    def __str__(self):
        estado = 'activa' if self.activa else 'dada de baja'
        return f'{self.etiqueta or "Credencial"} de {self.usuario} ({estado})'

    @staticmethod
    def normalizar(codigo):
        """El lector es un keyboard wedge: puede traer CR/LF o espacios."""
        return (codigo or '').strip()

    @classmethod
    def hash_codigo(cls, codigo):
        return hashlib.sha256(cls.normalizar(codigo).encode('utf-8')).hexdigest()

    @classmethod
    def registrar(cls, *, usuario, codigo, etiqueta='', creada_por=None):
        """Da de alta una credencial. El codigo crudo no se persiste."""
        codigo = cls.normalizar(codigo)
        if len(codigo) < cls.LONGITUD_MINIMA:
            raise ValueError(
                f'El codigo de la credencial debe tener al menos '
                f'{cls.LONGITUD_MINIMA} caracteres.'
            )
        return cls.objects.create(
            usuario=usuario,
            codigo_hash=cls.hash_codigo(codigo),
            etiqueta=(etiqueta or '')[:60],
            creada_por=creada_por,
        )

    @classmethod
    def resolver(cls, codigo):
        """
        Usuario detras de un codigo escaneado, o None.

        Devuelve None tambien si la credencial esta de baja o el usuario
        inactivo: una tarjeta reportada como perdida deja de servir sin
        tener que tocar el usuario, y un usuario desactivado no autoriza
        aunque su carnet siga circulando.
        """
        codigo = cls.normalizar(codigo)
        if len(codigo) < cls.LONGITUD_MINIMA:
            return None

        credencial = (
            cls.objects.select_related('usuario')
            .filter(codigo_hash=cls.hash_codigo(codigo), activa=True)
            .first()
        )
        if credencial is None:
            return None

        usuario = credencial.usuario
        if not usuario.is_active or not getattr(usuario, 'activo', True):
            return None
        return usuario

    def dar_de_baja(self):
        self.activa = False
        self.fecha_baja = timezone.now()
        self.save(update_fields=['activa', 'fecha_baja'])
