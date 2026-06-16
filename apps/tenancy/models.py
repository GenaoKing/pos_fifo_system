import hashlib

from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone
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
    media_prefix = models.CharField(max_length=160, blank=True)
    plan_slug = models.SlugField(max_length=100, blank=True)
    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(default=timezone.now)
    fecha_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tenancy_tenants'
        ordering = ['nombre']

    def __str__(self):
        return f'{self.nombre} ({self.tenant_key})'

    def save(self, *args, **kwargs):
        if self.tenant_key:
            self.tenant_key = self.tenant_key.lower().replace('-', '_')
        if not self.slug:
            self.slug = slugify(self.nombre)[:110] or self.tenant_key
        if not self.db_name:
            self.db_name = f'tnt_{self.tenant_key}'
        if not self.media_prefix:
            self.media_prefix = f'{self.tenant_key}/'
        super().save(*args, **kwargs)


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

    def __str__(self):
        return self.email

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

    def __str__(self):
        return f'{self.identity.email} -> {self.tenant.tenant_key}/{self.username}'


class Domain(models.Model):
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
