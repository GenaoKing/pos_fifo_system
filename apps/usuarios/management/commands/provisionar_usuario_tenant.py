"""Alta autorizada de usuario operativo, RBAC y auditoria CT-01."""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.auditoria.models import Auditoria
from apps.negocios.models import Negocio
from apps.permisos.models import Rol
from apps.sucursales.models import Sucursal
from apps.tenancy.context import force_tenancy, tenant_context
from apps.tenancy.management.base import TenantCommandMixin
from apps.tenancy.registry import configure_tenant_database, tenant_alias
from apps.usuarios.services import ProvisioningUsuarioError, provisionar_usuario


class Command(TenantCommandMixin, BaseCommand):
    help = 'Provisiona usuario + rol en una BD tenant; requiere actor autorizado.'

    def add_arguments(self, parser):
        self.add_tenant_argument(parser)
        parser.add_argument('--actor-username', required=True)
        parser.add_argument('--username', required=True)
        parser.add_argument('--email', required=True)
        parser.add_argument('--rol-slug', required=True)
        parser.add_argument('--sucursal-codigo', default='')
        parser.add_argument(
            '--password-env',
            default='POS_NEW_USER_PASSWORD',
            help='Variable de entorno con la clave inicial; nunca se imprime.',
        )

    def handle(self, *args, **options):
        tenant = self.get_tenant(options['tenant'])
        alias = tenant_alias(tenant.tenant_key)
        configure_tenant_database(tenant)
        password = os.environ.get(options['password_env'], '')
        if not password:
            raise CommandError(
                f'La variable {options["password_env"]} es obligatoria y no '
                'debe pasarse como argumento de linea de comandos.'
            )

        with force_tenancy(True), tenant_context(tenant):
            User = get_user_model()
            actor = User.objects.using(alias).filter(
                username__iexact=options['actor_username'].strip(), activo=True,
            ).first()
            if actor is None:
                raise CommandError('Actor operativo inexistente o inactivo.')
            negocio = Negocio.self_row()
            if negocio is None:
                raise CommandError('La BD tenant no tiene self-row Negocio.')
            rol = Rol.objects.using(alias).filter(
                negocio=negocio,
                slug=options['rol_slug'].strip(),
                activo=True,
            ).first()
            if rol is None:
                raise CommandError('Rol inexistente, inactivo o de otro negocio.')
            sucursal = None
            if options['sucursal_codigo'].strip():
                sucursal = Sucursal.objects.using(alias).filter(
                    negocio=negocio,
                    codigo=options['sucursal_codigo'].strip().upper(),
                    activa=True,
                ).first()
                if sucursal is None:
                    raise CommandError('Sucursal inexistente, inactiva o de otro negocio.')
            try:
                usuario, asignacion = provisionar_usuario(
                    actor=actor,
                    negocio=negocio,
                    username=options['username'],
                    email=options['email'],
                    password=password,
                    rol=rol,
                    sucursal=sucursal,
                    canal=Auditoria.Canal.COMMAND,
                    using=alias,
                )
            except ProvisioningUsuarioError as exc:
                raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(
            f'Usuario {usuario.username} provisionado; asignacion {asignacion.pk}.'
        ))
