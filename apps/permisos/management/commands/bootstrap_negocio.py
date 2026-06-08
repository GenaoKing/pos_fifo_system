"""
manage.py bootstrap_negocio [--nombre "Royal Plast"]

Bootstrap del RBAC para una instalacion existente (idempotente):
  - Siembra el catalogo de permisos.
  - Crea un Negocio default (si no existe) y enlaza sucursales/usuarios huerfanos.
  - Crea los roles de sistema (Administrador / Cajero).
  - Asigna rol a cada usuario segun su rol legacy.

Si no se pasa --nombre, intenta derivarlo de ConfiguracionNegocio.nombre_negocio.
"""
from django.core.management.base import BaseCommand

from apps.negocios.models import Negocio
from apps.permisos import seed
from apps.permisos.models import AsignacionRol, Permiso, Rol
from apps.sucursales.models import Sucursal
from apps.usuarios.models import Usuario


class Command(BaseCommand):
    help = 'Inicializa Negocio + roles + asignaciones para una instalacion existente.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--nombre',
            type=str,
            default=None,
            help='Nombre del negocio a crear si aun no existe.',
        )

    def handle(self, *args, **options):
        nombre = options.get('nombre') or self._nombre_default()

        negocio = seed.bootstrap(
            NegocioModel=Negocio,
            SucursalModel=Sucursal,
            UsuarioModel=Usuario,
            RolModel=Rol,
            PermisoModel=Permiso,
            AsignacionRolModel=AsignacionRol,
            nombre=nombre,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f'Bootstrap completo. Negocio: "{negocio.nombre}" (slug={negocio.slug}). '
                f'Sucursales: {negocio.sucursales.count()}, '
                f'Roles: {negocio.roles.count()}, '
                f'Usuarios: {Usuario.objects.filter(negocio=negocio).count()}.'
            )
        )

    def _nombre_default(self):
        """Deriva el nombre del negocio de la configuracion existente."""
        try:
            from apps.configuracion.models import ConfiguracionNegocio
            config = ConfiguracionNegocio.objects.exclude(
                nombre_negocio=''
            ).order_by('id').first()
            if config and config.nombre_negocio:
                return config.nombre_negocio
        except Exception:
            pass
        return 'Mi Negocio'
