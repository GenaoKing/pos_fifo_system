"""
manage.py bootstrap_negocio [--nombre "Royal Plast"]

Bootstrap del RBAC para una instalacion existente (idempotente):
  - Siembra el catalogo de permisos.
  - Crea un Negocio default (si no existe) y enlaza sucursales/usuarios huerfanos.
  - Crea los roles de sistema (Administrador / Cajero).
  - Asigna rol a cada usuario segun su rol legacy.

Si no se pasa --nombre, intenta derivarlo de ConfiguracionNegocio.nombre_negocio.
"""
import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, ProgrammingError, transaction

from apps.negocios.models import Negocio
from apps.permisos import seed
from apps.permisos.models import AsignacionRol, Permiso, Rol
from apps.sucursales.models import Sucursal
from apps.tenancy.context import get_current_tenant_alias
from apps.tenancy.management.base import TenantCommandMixin
from apps.usuarios.models import Usuario

logger = logging.getLogger(__name__)


class Command(TenantCommandMixin, BaseCommand):
    help = 'Inicializa Negocio + roles + asignaciones para una instalacion existente.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--nombre',
            type=str,
            default=None,
            help='Nombre del negocio a crear si aun no existe.',
        )
        parser.add_argument(
            '--negocio-id',
            type=int,
            default=None,
            help='Negocio explicito. Obligatorio si la base contiene varios.',
        )
        self.add_tenant_argument(parser, required=False)

    def handle(self, *args, **options):
        tenant_key = options.get('tenant')
        if tenant_key:
            tenant = self.get_tenant(tenant_key)
            return self.run_in_tenant(tenant, lambda: self._bootstrap(options))
        if getattr(settings, 'TENANCY_DB_PER_TENANT_ENABLED', False):
            raise CommandError('--tenant es obligatorio con tenancy habilitado.')
        return self._bootstrap(options)

    def _bootstrap(self, options):
        nombre = options.get('nombre') or self._nombre_default()
        negocio_id = options.get('negocio_id')
        negocio = None
        if negocio_id is not None:
            negocio = Negocio.objects.filter(pk=negocio_id).first()
            if negocio is None:
                raise CommandError(f'Negocio id={negocio_id} no existe.')

        alias = get_current_tenant_alias() or 'default'
        try:
            with transaction.atomic(using=alias):
                negocio = seed.bootstrap(
                    NegocioModel=Negocio,
                    SucursalModel=Sucursal,
                    UsuarioModel=Usuario,
                    RolModel=Rol,
                    PermisoModel=Permiso,
                    AsignacionRolModel=AsignacionRol,
                    nombre=nombre,
                    negocio=negocio,
                    using=alias,
                )
                from apps.notificaciones.seed import crear_reglas_default
                crear_reglas_default(negocio)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

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
        except (OperationalError, ProgrammingError) as exc:
            logger.warning(
                'No se pudo derivar el nombre desde ConfiguracionNegocio: %s', exc,
            )
        return 'Mi Negocio'
