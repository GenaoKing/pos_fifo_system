import hashlib

from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone
from django.db.models.functions import Lower
from django.utils.text import slugify


class Tenant(models.Model):
    tenant_key = models.SlugField(
        max_length=64,
        unique=True,
        help_text='Identificador tecnico estable. No cambia sin migracion.',
    )
    slug = models.SlugField(max_length=120, unique=True)
    nombre = models.CharField(max_length=200)
    rnc = models.CharField(max_length=20, blank=True)
    db_name = models.CharField(max_length=128, unique=True, blank=True)
    # UNICO: es el namespace de archivos del tenant. Sin unicidad, dos negocios
    # podian tener `media_prefix='shared/'` y resolver exactamente el mismo path
    # de Blob: un upload sobrescribia el logo o la foto del otro.
    media_prefix = models.CharField(max_length=160, unique=True, blank=True)
    plan_slug = models.SlugField(max_length=100, blank=True)
    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(default=timezone.now)
    fecha_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tenancy_tenants'
        ordering = ['nombre']

    # Campos que identifican al tenant para ROUTING y almacenamiento. Cambiarlos
    # en caliente parte el sistema: los workers que ya tocaron el alias siguen
    # con la conexion vieja, los tokens emitidos dejan de resolver y los blobs
    # quedan en el namespace anterior. Se declaran aca para que el admin los
    # muestre de solo lectura y para documentar la invariante en el modelo.
    CAMPOS_INMUTABLES = ('tenant_key', 'db_name', 'media_prefix')

    def __str__(self):
        return f'{self.nombre} ({self.tenant_key})'

    def save(self, *args, **kwargs):
        if self.tenant_key:
            self.tenant_key = self.tenant_key.lower().replace('-', '_')
        if not self.slug:
            self.slug = self._slug_unico(self.nombre, tenant_key=self.tenant_key)
        if not self.db_name:
            self.db_name = f'tnt_{self.tenant_key}'
        # El prefijo NUNCA queda vacio: un prefijo vacio degrada las rutas a
        # globales aunque tenancy este encendido, y dos tenants vacios colisionan
        # entre si en el container compartido.
        if not (self.media_prefix or '').strip(' /'):
            self.media_prefix = f'{self.tenant_key}/'
        super().save(*args, **kwargs)

    @classmethod
    def _slug_unico(cls, nombre, *, tenant_key='', exclude_pk=None):
        base = slugify(nombre)[:100] or slugify(tenant_key)[:100] or 'tenant'
        db = cls.objects.db

        def exists(candidate):
            qs = cls.objects.using(db).filter(slug=candidate)
            if exclude_pk:
                qs = qs.exclude(pk=exclude_pk)
            return qs.exists()

        if not exists(base):
            return base

        tenant_suffix = slugify(tenant_key)[:20]
        if tenant_suffix:
            candidate = f'{base}-{tenant_suffix}'[:120]
            if not exists(candidate):
                return candidate

        i = 2
        while True:
            suffix = f'-{i}'
            candidate = f'{base[:120 - len(suffix)]}{suffix}'
            if not exists(candidate):
                return candidate
            i += 1


class Identity(models.Model):
    email = models.EmailField(unique=True)
    password = models.CharField(max_length=128)
    nombre = models.CharField(max_length=200, blank=True)
    activo = models.BooleanField(default=True)
    is_global = models.BooleanField(default=False)
    fecha_creacion = models.DateTimeField(default=timezone.now)
    fecha_modificacion = models.DateTimeField(auto_now=True)
    ultimo_acceso = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'tenancy_identities'
        ordering = ['email']
        constraints = [
            # El login normaliza a minusculas y busca `email__iexact`, pero la
            # unicidad de BD es sensible a mayusculas en PostgreSQL: la tabla
            # aceptaba `Owner@Example.com` y `owner@example.com` a la vez, y el
            # login elegia una u otra segun el orden de las filas.
            models.UniqueConstraint(
                Lower('email'), name='uniq_identity_email_lower',
            ),
        ]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        # Normaliza en escritura, para que la constraint no dependa de que
        # todos los callers se acuerden.
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    @property
    def is_authenticated(self):
        return True

    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.password)


class Membership(models.Model):
    identity = models.ForeignKey(
        Identity, on_delete=models.CASCADE, related_name='memberships'
    )
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name='memberships'
    )
    username = models.CharField(max_length=150)
    rol = models.CharField(max_length=20, default='ADMIN')
    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(default=timezone.now)
    fecha_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tenancy_memberships'
        unique_together = ('identity', 'tenant')
        ordering = ['identity__email', 'tenant__tenant_key']
        constraints = [
            # Un usuario operativo del tenant pertenece a UNA identidad global.
            # Sin esto, dos identities distintas podian mapear al mismo
            # `tenant/username=admin`: dos credenciales globales actuando como
            # el mismo usuario, y una auditoria basada en `Usuario` no podia
            # distinguir quien hizo que.
            models.UniqueConstraint(
                fields=['tenant', 'username'],
                name='uniq_membership_tenant_username',
            ),
        ]

    def __str__(self):
        return f'{self.identity.email} -> {self.tenant.tenant_key}/{self.username}'


class Domain(models.Model):
    """
    Modelo PREPARATORIO: no participa en la resolucion de tenant.

    Ningun codigo fuera de este modulo lo consulta. Antes de habilitarlo hacen
    falta normalizacion (IDNA, lower, puerto), un solo `is_primary` activo por
    tenant y rechazo de hosts reservados. Hasta entonces el admin lo expone
    como solo lectura para no aparentar un routing que no existe.
    """

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='domains')
    domain = models.CharField(max_length=255, unique=True)
    is_primary = models.BooleanField(default=True)
    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'tenancy_domains'
        ordering = ['domain']

    def __str__(self):
        return self.domain


class SesionImpersonacion(models.Model):
    """
    Rastro durable de una impersonacion de soporte.

    Antes no quedaba ninguno. `impersonar_tenant` emitia un token que actuaba
    como un `Usuario` operativo del tenant, y el `identity_id` del operador
    global viajaba solo como atributo en memoria. Ademas el middleware de
    auditoria omite deliberadamente todo `/api/` bajo tenancy, asi que una
    accion de soporte quedaba atribuida al admin local impersonado — o no
    quedaba en ningun lado.

    Vive en el CONTROL PLANE a proposito: el actor es global y su trazabilidad
    no debe depender de la base del tenant al que entro (ni poder alterarse
    desde ella).
    """

    identity = models.ForeignKey(
        Identity,
        on_delete=models.PROTECT,
        related_name='impersonaciones',
        help_text='Actor global real que ejecuto la accion.',
    )
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.PROTECT,
        related_name='impersonaciones',
    )
    username_objetivo = models.CharField(
        max_length=150,
        help_text='Usuario operativo del tenant bajo el que se actuo.',
    )
    motivo = models.CharField(
        max_length=300,
        blank=True,
        help_text='Ticket o razon declarada por el operador.',
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    inicio = models.DateTimeField(default=timezone.now, db_index=True)
    expira = models.DateTimeField(
        null=True, blank=True,
        help_text='Vencimiento del token emitido.',
    )
    cierre = models.DateTimeField(
        null=True, blank=True,
        help_text='Logout explicito de la sesion impersonada.',
    )

    class Meta:
        db_table = 'tenancy_sesiones_impersonacion'
        ordering = ['-inicio']
        indexes = [
            models.Index(fields=['tenant', 'inicio']),
            models.Index(fields=['identity', 'inicio']),
        ]

    def __str__(self):
        return (
            f'{self.identity.email} -> {self.tenant.tenant_key}/'
            f'{self.username_objetivo} ({self.inicio:%Y-%m-%d %H:%M})'
        )


class SyncToken(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='sync_tokens')
    token_hash = models.CharField(max_length=64, unique=True)
    sucursal_codigo = models.CharField(max_length=20)
    activo = models.BooleanField(default=True)
    descripcion = models.CharField(max_length=200, blank=True)
    fecha_creacion = models.DateTimeField(default=timezone.now)
    fecha_modificacion = models.DateTimeField(auto_now=True)
    ultimo_uso = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'tenancy_sync_tokens'
        unique_together = ('tenant', 'sucursal_codigo')
        ordering = ['tenant__tenant_key', 'sucursal_codigo']

    def __str__(self):
        return f'{self.tenant.tenant_key}:{self.sucursal_codigo}'

    @staticmethod
    def hash_token(token):
        return hashlib.sha256(token.encode('utf-8')).hexdigest()
