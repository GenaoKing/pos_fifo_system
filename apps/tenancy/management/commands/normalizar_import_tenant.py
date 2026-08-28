from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from rest_framework.authtoken.models import Token

from django.db import transaction

from apps.tenancy.context import force_tenancy, tenant_context
from apps.tenancy.registry import configure_tenant_database, tenant_alias
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
        parser.add_argument(
            '--admin-password',
            help='Password inicial del admin. Obligatoria si el admin todavia no '
                 'tiene una, o junto con --rotar-password. Sin ella, un rerun '
                 'conserva la credencial vigente.',
        )
        parser.add_argument(
            '--rotar-password',
            action='store_true',
            help='Restablece la password del admin y de la Identity. Sin este '
                 'flag no se toca ninguna credencial existente.',
        )
        parser.add_argument('--admin-username', default='', help='Username operativo admin. Default: primer ADMIN/SYSADMIN.')
        parser.add_argument('--plan', default='empresarial', help='Plan a asignar si existe.')
        parser.add_argument('--dry-run', action='store_true', help='Reporta conteos sin escribir.')
        parser.add_argument(
            '--show-sync-token',
            action='store_true',
            help='Muestra el token sync plano en stdout. Por defecto se enmascara.',
        )
        parser.add_argument(
            '--force-sucursal-backfill',
            action='store_true',
            help='Asigna filas con sucursal nula a la sucursal inicial aun cuando '
                 'existe mas de una sucursal (import multi-sucursal explicito).',
        )

    def handle(self, *args, **options):
        tenant = self.get_tenant(options['tenant'])
        admin_email = options['admin_email'].strip().lower()
        if not admin_email:
            raise CommandError('--admin-email no puede estar vacio.')
        options['admin_email'] = admin_email
        self._validate_admin_email_available(tenant, admin_email)

        # Preflight de credenciales: si hay que fijar una password y no vino,
        # se falla ANTES de tocar nada.
        identity_existente = Identity.objects.using('default').filter(
            email__iexact=admin_email,
        ).exists()
        if options.get('rotar_password') and not options.get('admin_password'):
            raise CommandError(
                '--rotar-password requiere --admin-password.'
            )
        if not identity_existente and not options.get('admin_password'):
            raise CommandError(
                'La Identity admin no existe todavia: pasa --admin-password para '
                'crearla. (Ya no hay password por defecto.)'
            )

        # ATOMICIDAD POR BASE. Son dos bases distintas, asi que no hay una sola
        # transaccion posible; lo que si se puede es que CADA dominio revierta
        # entero.
        #
        # `using=alias` es obligatorio: `transaction.atomic()` sin alias abre la
        # transaccion sobre `default` aunque el router mande los modelos a la
        # base del tenant. Sin esto, un fallo a mitad dejaba el origen a medio
        # transformar (negocio y sucursal ya cambiados, backfills sin hacer).
        if options['dry_run']:
            # Dry-run no escribe: no hace falta registrar el alias ni abrir una
            # transaccion contra la base del tenant.
            with force_tenancy(True):
                with tenant_context(tenant):
                    summary = self._normalize_tenant_db(tenant, options)
        else:
            alias = tenant_alias(tenant.tenant_key)
            configure_tenant_database(tenant)
            with force_tenancy(True):
                with tenant_context(tenant):
                    with transaction.atomic(using=alias):
                        summary = self._normalize_tenant_db(tenant, options)

        if not options['dry_run']:
            # El cruce entre bases sigue sin ser atomico: si el control plane
            # falla, la base tenant ya quedo normalizada. Es reanudable — el
            # comando es idempotente y ya no toca credenciales sin `--rotar-
            # password` — pero conviene saberlo al leer un fallo.
            try:
                with transaction.atomic(using='default'):
                    self._normalize_control_plane(
                        tenant, options, summary['admin_username'], summary['sync_token'],
                    )
            except Exception:
                self.stderr.write(self.style.ERROR(
                    'La base del tenant quedo normalizada pero el control plane '
                    'fallo. Re-ejecuta el comando: la fase tenant es idempotente.'
                ))
                raise

        for key, value in summary.items():
            if key == 'sync_token' and not options['show_sync_token']:
                value = value[:8] + '...' if value else ''
            self.stdout.write(f'{key}: {value}')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('DRY-RUN: no se escribieron cambios.'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Import normalizado: {tenant.tenant_key}.'))

    def _preflight(self, options, Sucursal, modelos_con_sucursal):
        """
        Valida la BD origen ANTES de tocarla. Corre igual en dry-run.

        Backfilear `sucursal` nula a la sucursal inicial solo es correcto si la
        base venia de una instalacion mono-sucursal. Con varias sucursales, ese
        backfill atribuye mal ventas, compras, lotes y caja — y eso no se
        deshace mirando los datos despues.
        """
        codigo_inicial = options['sucursal_codigo'].upper()

        otras_sucursales = Sucursal.objects.exclude(codigo=codigo_inicial).exists()
        hay_filas_sin_sucursal = any(
            modelo.objects.filter(sucursal__isnull=True).exists()
            for modelo in modelos_con_sucursal
        )

        if otras_sucursales and hay_filas_sin_sucursal and not options.get('force_sucursal_backfill'):
            raise CommandError(
                'La BD origen tiene mas de una sucursal y filas con sucursal nula: '
                'el backfill las asignaria todas a la sucursal inicial y atribuiria '
                'mal los datos. Resuelve la atribucion manualmente o re-ejecuta con '
                '--force-sucursal-backfill si es intencional.'
            )

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

        # PREFLIGHT: todas las validaciones ANTES de la primera escritura, y
        # tambien en dry-run.
        #
        # El guard multi-sucursal corria DESPUES de crear/editar el negocio,
        # crear la sucursal inicial y asignar negocio a todos los usuarios sin
        # el. Cuando saltaba, el comando informaba fallo pero la BD origen ya
        # estaba parcialmente transformada — y como no habia transaccion, nada
        # lo revertia.
        self._preflight(options, Sucursal, (Venta, Compra, Lote, Caja))

        # Misma regla que bootstrap: si hay mas de una fila, detenerse (NEG-005).
        negocio = Negocio.self_row()
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
            # El default literal desaparecio: normalizar un import NO puede
            # reemplazar en silencio la password del dueno por una conocida.
            if options.get('rotar_password'):
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
        identity, identity_creada = Identity.objects.using('default').get_or_create(
            email=options['admin_email'],
            defaults={'nombre': options['nombre'], 'activo': True},
        )
        identity.nombre = options['nombre']
        identity.activo = True
        if identity_creada or options.get('rotar_password'):
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

    def _validate_admin_email_available(self, tenant, admin_email):
        conflicting_membership = (
            Membership.objects.using('default')
            .select_related('tenant', 'identity')
            .filter(identity__email__iexact=admin_email, activo=True)
            .exclude(tenant__tenant_key=tenant.tenant_key)
            .first()
        )
        if conflicting_membership is not None:
            raise CommandError(
                f'El admin-email "{admin_email}" ya tiene una membresia activa '
                f'en el tenant "{conflicting_membership.tenant.tenant_key}". '
                'Use otro email o una Identity global con impersonation.'
            )

    def _first_admin_username(self, User):
        user = User.objects.filter(rol__in=('SYSADMIN', 'ADMIN'), activo=True).order_by('id').first()
        return user.username if user else ''
