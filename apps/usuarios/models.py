from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class UsuarioManager(BaseUserManager):
    """Manager personalizado para el modelo Usuario"""
    
    def create_user(self, username, email, password=None, **extra_fields):
        """Crea y guarda un usuario regular"""
        if not username:
            raise ValueError('El usuario debe tener un nombre de usuario')
        if not email:
            raise ValueError('El usuario debe tener un email')
        
        email = self.normalize_email(email)
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user
    
    def create_superuser(self, username, email, password=None, **extra_fields):
        """Crea y guarda un superusuario"""
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('rol', 'ADMIN')
        extra_fields.setdefault('activo', True)
        
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser debe tener is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser debe tener is_superuser=True.')
        
        return self.create_user(username, email, password, **extra_fields)


class Usuario(AbstractBaseUser, PermissionsMixin):
    """
    Modelo de usuario personalizado para el sistema POS.
    Extiende AbstractBaseUser para tener control total sobre los campos.
    """
    
    ROLES = (
        ('SYSADMIN', 'Administrador de Sistema'),
        ('ADMIN', 'Administrador'),
        ('CAJERA', 'Cajera'),
    )
    
    # Campos básicos
    username = models.CharField(
        'Nombre de usuario',
        max_length=150,
        unique=True,
        help_text='Requerido. 150 caracteres o menos.',
    )
    email = models.EmailField(
        'Correo electrónico',
        unique=True,
    )
    first_name = models.CharField('Nombre', max_length=150, blank=True)
    last_name = models.CharField('Apellido', max_length=150, blank=True)
    
    # Negocio (tenant) al que pertenece el usuario.
    # Null = usuario global (ej. SYSADMIN). Alimenta el claim tenant_id del JWT.
    negocio = models.ForeignKey(
        'negocios.Negocio',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='usuarios',
        verbose_name='Negocio',
    )

    # Rol del usuario.
    # NOTA: legacy. El enforcement real vive en el motor RBAC
    # (apps.permisos / AsignacionRol). Este campo se conserva como informativo
    # y como semilla de la migracion; 'SYSADMIN' aun otorga acceso total.
    rol = models.CharField(
        'Rol',
        max_length=10,
        choices=ROLES,
        default='CAJERA',
    )
    
    # Estado y fechas
    activo = models.BooleanField(
        'Activo',
        default=True,
        help_text='Indica si el usuario puede iniciar sesión.',
    )
    is_staff = models.BooleanField(
        'Es staff',
        default=False,
        help_text='Indica si el usuario puede acceder al admin.',
    )
    
    fecha_creacion = models.DateTimeField('Fecha de creación', default=timezone.now)
    fecha_modificacion = models.DateTimeField('Fecha de modificación', auto_now=True)
    ultimo_acceso = models.DateTimeField('Último acceso', null=True, blank=True)
    
    # Manager personalizado
    objects = UsuarioManager()
    
    # Configuración de autenticación
    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['email']
    
    class Meta:
        verbose_name = 'Usuario'
        verbose_name_plural = 'Usuarios'
        ordering = ['-fecha_creacion']
        db_table = 'usuarios'
    
    def __str__(self):
        return f"{self.username} ({self.get_rol_display()})"
    
    def get_full_name(self):
        """Retorna el nombre completo del usuario"""
        return f"{self.first_name} {self.last_name}".strip() or self.username
    
    def get_short_name(self):
        """Retorna el nombre corto del usuario"""
        return self.first_name or self.username
    
    @property
    def is_active(self):
        """
        Contrato de Django para "cuenta habilitada", ligado a `activo`.

        `AbstractBaseUser` define `is_active = True` como atributo de clase, y
        este modelo nunca lo redefinia: Django veia activo a TODO usuario. El
        login local si miraba `activo`, pero solo al iniciar sesion — una sesion
        ya emitida se recargaba por el backend estandar, que mira `is_active`, y
        sobrevivia a la desactivacion. Unificarlos hace que desactivar tenga
        efecto en el proximo request (PER-010).
        """
        return bool(self.activo)

    @is_active.setter
    def is_active(self, valor):
        # `createsuperuser` y algunos flujos de Django escriben is_active.
        self.activo = bool(valor)

    @property
    def es_admin(self):
        """SYSADMIN tambien es admin a efectos de permisos"""
        return self.rol in ('ADMIN', 'SYSADMIN')
    
    @property
    def es_sysadmin(self):
        """Retorna True si el usuario es sysadmin"""
        return self.rol == 'SYSADMIN'

    @property
    def es_cajera(self):
        """Retorna True si el usuario es cajera"""
        return self.rol == 'CAJERA'
    
    def tiene_permiso(self, permiso, sucursal=None):
        """
        Verifica si el usuario tiene un permiso (codigo del catalogo, ej.
        'clientes.crear'). Delega en el motor RBAC data-driven, que resuelve
        los permisos a partir de los roles asignados al usuario en su negocio.

        Default deny: salvo SYSADMIN/superuser (acceso total), un usuario sin
        roles que otorguen el permiso recibe False.

        `sucursal` acota la resolucion a las asignaciones de esa sucursal
        (mas las globales del negocio).
        """
        from apps.permisos.engine import tiene_permiso as _tiene_permiso
        return _tiene_permiso(self, permiso, sucursal=sucursal)
