from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from rest_framework.authtoken.models import Token

from apps.tenancy.context import force_tenancy, tenant_context
from apps.tenancy.management.base import TenantCommandMixin
from apps.tenancy.models import Identity, Membership, SyncToken


class Command(TenantCommandMixin, BaseCommand):
    help = 'Normaliza una BD mono-tenant restaurada para operar como tenant DB.'

    def add_arguments(self, parser):
        self.add_tenant_argument(parser)
        parser.add_argument('--nombre', required=True, help='Nombre del negocio self-row.')
        parser.add_argument('--slug', required=True, help='Slug del negocio self-row.')
        parser.add_argument('--rnc', default='', help='RNC del negocio.')
        parser.add_argument('--sucursal-codigo', required=True, help='Codigo de sucursal inicial.')
        parser.add_argument('--sucursal-nombre', required=True, help='Nombre de sucursal inicial.')
        parser.add_argument('--admin-email', required=True, help='Email de la Identity admin.')
        parser.add_argument('--admin-password', default='Admin123!', help='Password inicial.')
        parser.add_argument('--admin-username', default='', help='Username operativo admin. Default: primer ADMIN/SYSADMIN.')
        parser.add_argument('--plan', default='empresarial', help='Plan a asignar si existe.')
        parser.add_argument('--dry-run', action='store_true', help='Reporta conteos sin escribir.')
        parser.add_argument(
            '--force-sucursal-backfill',
            action='store_true',
            help='Asigna filas con sucursal nula a la sucursal inicial aun cuando '
                 'existe mas de una sucursal (import multi-sucursal explicito).',
        )

    def handle(self, *args, **options):
        tenant = self.get_tenant(options['tenant'])

        with force_tenancy(True):
            with tenant_context(tenant):
                summary = self._normalize_tenant_db(tenant, options)

        if not options['dry_run']:
            self._normalize_control_plane(tenant, options, summary['admin_username'], summary['sync_token'])

        for key, value in summary.items():
            if key == 'sync_token':
                value = value[:8] + '...' if value else ''
            self.stdout.write(f'{key}: {value}')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('DRY-RUN: no se escribieron cambios.'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Import normalizado: {tenant.tenant_key}.'))

    def _normalize_tenant_db(self, tenant, options):
        from apps.caja.models import Caja
        from apps.configuracion.models import ConfiguracionNegocio
        from apps.inventario.models import Compra, Lote
        from apps.negocios.models import Negocio
        from apps.permisos.models import AsignacionRol, Permiso, Rol
        from apps.permisos.seed import bootstrap as bootstrap_rbac
        from apps.sucursales.models import Sucursal
        from apps.suscripciones import seed as suscripciones_seed
        from apps.suscripciones.models import (
            Modulo,
            NegocioModulo,
            Plan,
            SuscripcionNegocio,
        )
        from apps.ventas.models import Venta

        dry_run = options['dry_run']
        User = get_user_model()

        negocio = Negocio.objects.order_by('id').first()
        if negocio is None:
            if dry_run:
                negocio_id = '(nuevo)'
            else:
                negocio = Negocio.objects.create(
                    nombre=options['nombre'],
                    slug=options['slug'],
                    rnc=options['rnc'],
                    activo=True,
                )
                negocio_id = negocio.pk
        else:
            negocio_id = negocio.pk
            if not dry_run:
                negocio.nombre = options['nombre']
                negocio.slug = options['slug']
                negocio.rnc = options['rnc']
                negocio.activo = True
                negocio.save(update_fields=['nombre', 'slug', 'rnc', 'activo', 'fecha_modificacion'])

        if dry_run:
            sucursal = None
        else:
            sucursal, _ = Sucursal.objects.update_or_create(
                codigo=options['sucursal_codigo'].upper(),
                defaults={
                    'nombre': options['sucursal_nombre'],
                    'negocio': negocio,
                    'activa': True,
                },
            )

        if not dry_run:
            User.objects.filter(negocio__isnull=True).update(negocio=negocio)

            # Guard: backfilear sucursal nula a la sucursal inicial solo es correcto
            # si la BD origen es mono-sucursal. Con varias sucursales atribuiria mal
            # ventas/compras/lotes/caja -> fail-loud salvo opt-in explicito.
            otras_sucursales = Sucursal.objects.exclude(pk=sucursal.pk).exists()
            hay_filas_sin_sucursal = any(
                modelo.objects.filter(sucursal__isnull=True).exists()
                for modelo in (Venta, Compra, Lote, Caja)
            )
            if otras_sucursales and hay_filas_sin_sucursal and not options.get('force_sucursal_backfill'):
                raise CommandError(
                    'La BD origen tiene mas de una sucursal y filas con sucursal nula: '
                    'el backfill las asignaria todas a la sucursal inicial y atribuiria '
                    'mal los datos. Resuelve la atribucion manualmente o re-ejecuta con '
                    '--force-sucursal-backfill si es intencional.'
                )

            Venta.objects.filter(sucursal__isnull=True).update(sucursal=sucursal)
            Compra.objects.filter(sucursal__isnull=True).update(sucursal=sucursal)
            Lote.objects.filter(sucursal__isnull=True).update(sucursal=sucursal)
            Caja.objects.filter(sucursal__isnull=True).update(sucursal=sucursal)

            config = ConfiguracionNegocio.objects.order_by('id').first()
            if config is None:
                ConfiguracionNegocio.objects.create(
                    sucursal=sucursal,
                    nombre_negocio=options['nombre'],
                    rnc=options['rnc'],
                )
            elif config.sucursal_id is None:
                config.sucursal = sucursal
                config.nombre_negocio = options['nombre']
                if options['rnc']:
                    config.rnc = options['rnc']
                config.save()

            bootstrap_rbac(
                NegocioModel=Negocio,
                SucursalModel=Sucursal,
                UsuarioModel=User,
                RolModel=Rol,
                PermisoModel=Permiso,
                AsignacionRolModel=AsignacionRol,
                nombre=options['nombre'],
            )
            suscripciones_seed.bootstrap(
                ModuloModel=Modulo,
                PlanModel=Plan,
                NegocioModel=Negocio,
                NegocioModuloModel=NegocioModulo,
                SuscripcionModel=SuscripcionNegocio,
                ConfiguracionModel=ConfiguracionNegocio,
            )
            plan = Plan.objects.filter(slug=options['plan']).first()
            if plan is not None:
                SuscripcionNegocio.objects.update_or_create(
                    negocio=negocio,
                    defaults={'plan': plan, 'activa': True},
                )

        admin_username = options['admin_username'] or self._first_admin_username(User)
        if not admin_username:
            raise CommandError('No hay usuario ADMIN/SYSADMIN y no se paso --admin-username.')

        sync_token = ''
        if not dry_run:
            admin = User.objects.filter(username=admin_username).first()
            if admin is None:
                raise CommandError(f'Usuario admin "{admin_username}" no existe.')
            admin.email = options['admin_email']
            admin.negocio = negocio
            admin.activo = True
            admin.rol = 'SYSADMIN' if admin.rol == 'SYSADMIN' else 'ADMIN'
            admin.set_password(options['admin_password'])
            admin.save()

            service_username = f'sucursal_service_{options["sucursal_codigo"].upper()}'
            service_user, _ = User.objects.get_or_create(
                username=service_username,
                defaults={
                    'email': f'{service_username.lower()}@{tenant.tenant_key}.sync.local',
                    'first_name': 'Sucursal',
                    'last_name': options['sucursal_codigo'].upper(),
                    'rol': 'CAJERA',
                    'activo': True,
                    'negocio': negocio,
                },
            )
            service_user.negocio = negocio
            service_user.activo = True
            service_user.set_unusable_password()
            service_user.save()
            sucursal.usuario_servicio = service_user
            sucursal.save(update_fields=['usuario_servicio'])
            token, _ = Token.objects.get_or_create(user=service_user)
            sync_token = token.key

        return {
            'negocio_id': negocio_id,
            'sucursales': Sucursal.objects.count(),
            'usuarios_sin_negocio': User.objects.filter(negocio__isnull=True).count(),
            'ventas_sin_sucursal': Venta.objects.filter(sucursal__isnull=True).count(),
            'compras_sin_sucursal': Compra.objects.filter(sucursal__isnull=True).count(),
            'lotes_sin_sucursal': Lote.objects.filter(sucursal__isnull=True).count(),
            'admin_username': admin_username,
            'sync_token': sync_token,
        }

    def _normalize_control_plane(self, tenant, options, admin_username, sync_token):
        identity, _ = Identity.objects.using('default').get_or_create(
            email=options['admin_email'].lower(),
            defaults={'nombre': options['nombre'], 'activo': True},
        )
        identity.nombre = options['nombre']
        identity.activo = True
        identity.set_password(options['admin_password'])
        identity.save()

        Membership.objects.using('default').update_or_create(
            identity=identity,
            tenant=tenant,
            defaults={'username': admin_username, 'rol': 'ADMIN', 'activo': True},
        )

        SyncToken.objects.using('default').update_or_create(
            tenant=tenant,
            sucursal_codigo=options['sucursal_codigo'].upper(),
            defaults={
                'token_hash': SyncToken.hash_token(sync_token),
                'activo': True,
                'descripcion': f'Sync {tenant.tenant_key}/{options["sucursal_codigo"].upper()}',
            },
        )

    def _first_admin_username(self, User):
        user = User.objects.filter(rol__in=('SYSADMIN', 'ADMIN'), activo=True).order_by('id').first()
        return user.username if user else ''
