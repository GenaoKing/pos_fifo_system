import secrets

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify
from rest_framework.authtoken.models import Token

from apps.tenancy.context import force_tenancy, tenant_context
from apps.tenancy.db import create_database, database_exists
from apps.tenancy.models import Identity, Membership, SyncToken, Tenant
from apps.tenancy.registry import configure_tenant_database


class Command(BaseCommand):
    help = 'Bootstrap idempotente del control plane y la BD operativa de un tenant.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', required=True, help='tenant_key tecnico. Ej: demo.')
        parser.add_argument('--nombre', required=True, help='Nombre comercial/legal.')
        parser.add_argument('--slug', help='Slug comercial. Default: derivado del nombre.')
        parser.add_argument('--rnc', default='', help='RNC del negocio.')
        parser.add_argument('--admin-email', help='Email de la Identity admin.')
        parser.add_argument(
            '--admin-password',
            help='Password inicial de la Identity admin. Obligatorio al CREAR. '
                 'Si se omite en un alta, se genera uno aleatorio y se muestra '
                 'una sola vez.',
        )
        parser.add_argument(
            '--rotar-password',
            action='store_true',
            help='Restablece la password del admin aunque ya exista. Sin este '
                 'flag, un rerun conserva la credencial vigente.',
        )
        parser.add_argument(
            '--mostrar-token',
            action='store_true',
            help='Imprime el token sync completo. Por defecto se enmascara: '
                 'los logs de CI y de jobs lo retienen.',
        )
        parser.add_argument('--admin-username', default='admin', help='Username operativo.')
        parser.add_argument('--sucursal-codigo', default='SD-001', help='Codigo de sucursal inicial.')
        parser.add_argument('--sucursal-nombre', help='Nombre de sucursal inicial.')
        parser.add_argument('--plan', default='empresarial', help='Plan inicial en la BD tenant.')
        parser.add_argument('--dry-run', action='store_true', help='Reporta acciones sin escribir.')
        parser.add_argument('--skip-migrate', action='store_true', help='No correr migrate_tenants.')

    def handle(self, *args, **opts):
        tenant_key = opts['tenant'].strip().lower().replace('-', '_')
        nombre = opts['nombre'].strip()
        explicit_slug = bool(opts.get('slug'))
        raw_slug = (opts.get('slug') or '').strip()
        admin_email = (opts.get('admin_email') or f'admin@{tenant_key}.local').strip().lower()

        # NUNCA una password literal por defecto.
        #
        # Antes el default era una credencial conocida y publicada en runbooks, y
        # cada rerun llamaba `set_password` sobre el usuario operativo Y la
        # Identity: un bootstrap "idempotente" reemplazaba una password fuerte
        # por la conocida, bloqueando al dueno y abriendo acceso.
        admin_password, password_generada = self._resolver_password(
            opts.get('admin_password'), admin_email, tenant_key,
        )
        rotar_password = opts['rotar_password']
        mostrar_token = opts['mostrar_token']
        sucursal_codigo = opts['sucursal_codigo'].strip().upper()
        sucursal_nombre = opts.get('sucursal_nombre') or f'{nombre} - Principal'
        dry_run = opts['dry_run']

        existing_tenant = Tenant.objects.using('default').filter(tenant_key=tenant_key).first()
        if explicit_slug:
            slug = slugify(raw_slug) or tenant_key
            slug_owner = Tenant.objects.using('default').filter(slug=slug).exclude(
                tenant_key=tenant_key,
            ).first()
            if slug_owner is not None:
                raise CommandError(
                    f'El slug "{slug}" ya pertenece al tenant "{slug_owner.tenant_key}".'
                )
        elif existing_tenant is not None:
            slug = existing_tenant.slug
        else:
            slug = Tenant._slug_unico(nombre, tenant_key=tenant_key)

        conflicting_membership = (
            Membership.objects.using('default')
            .select_related('tenant', 'identity')
            .filter(identity__email__iexact=admin_email, activo=True)
            .exclude(tenant__tenant_key=tenant_key)
            .first()
        )
        if conflicting_membership is not None:
            raise CommandError(
                f'El admin-email "{admin_email}" ya tiene una membresia activa '
                f'en el tenant "{conflicting_membership.tenant.tenant_key}". '
                'Use otro email o una Identity global con impersonation.'
            )

        self.stdout.write('Bootstrap tenant')
        self.stdout.write(f'  tenant_key: {tenant_key}')
        self.stdout.write(f'  nombre:     {nombre}')
        self.stdout.write(f'  admin:      {admin_email} / {opts["admin_username"]}')
        self.stdout.write(f'  sucursal:   {sucursal_codigo}')
        self.stdout.write(f'  dry-run:    {dry_run}')

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN: no se escribiran cambios.'))
            self.stdout.write(f'Crearia/actualizaria Tenant + DB tnt_{tenant_key}.')
            self.stdout.write('Migraria la BD tenant y sembraria negocio, admin, sucursal y token.')
            return

        # EL TENANT SE PUBLICA AL FINAL, no al principio.
        #
        # Antes esto escribia `activo=True` y reemplazaba db_name/media_prefix
        # ANTES de verificar o crear la base. Si algo fallaba despues —crear la
        # BD, migrar, sembrar— la fila quedaba activa y enrutable apuntando a
        # una base que no existia o estaba a medio sembrar. Auth y migraciones
        # veian un tenant "listo" que no lo estaba.
        #
        # Ahora: se crea/actualiza INACTIVO, se aprovisiona, y solo si todo
        # salio bien se activa.
        tenant, tenant_creado = Tenant.objects.using('default').get_or_create(
            tenant_key=tenant_key,
            defaults={
                'slug': slug,
                'nombre': nombre,
                'rnc': opts.get('rnc', ''),
                'db_name': f'tnt_{tenant_key}',
                'media_prefix': f'{tenant_key}/',
                'plan_slug': opts.get('plan') or '',
                'activo': False,
            },
        )

        if tenant_creado:
            activo_previo = False
        else:
            activo_previo = tenant.activo
            # Datos comerciales SI se actualizan; la identidad de routing NO.
            # Reescribir db_name/media_prefix de un tenant existente cambiaria
            # a que base apunta y donde viven sus archivos: eso es una
            # migracion de tenant, no un rerun de bootstrap.
            tenant.slug = slug
            tenant.nombre = nombre
            tenant.rnc = opts.get('rnc', '')
            tenant.plan_slug = opts.get('plan') or ''
            tenant.activo = False   # se republica al final
            tenant.save()

        try:
            self._ensure_database(tenant)
            # El tenant esta inactivo a proposito mientras se aprovisiona.
            configure_tenant_database(tenant, permitir_inactivo=True)

            if not opts['skip_migrate']:
                call_command(
                    'migrate_tenants', tenant=tenant.tenant_key,
                    noinput=True, incluir_inactivos=True,
                )
        except Exception:
            # No se deja publicado lo que no quedo listo. Si ya estaba activo
            # antes, se restaura su estado previo: el bootstrap no debe dar de
            # baja un tenant que estaba operando.
            tenant.activo = activo_previo
            tenant.save(update_fields=['activo'])
            raise

        with force_tenancy(True):
            with tenant_context(tenant):
                result = self._seed_tenant(
                    tenant=tenant,
                    nombre=nombre,
                    slug=slug,
                    rnc=opts.get('rnc', ''),
                    admin_email=admin_email,
                    admin_password=admin_password,
                    rotar_password=rotar_password,
                    admin_username=opts['admin_username'],
                    sucursal_codigo=sucursal_codigo,
                    sucursal_nombre=sucursal_nombre,
                    plan_slug=opts.get('plan') or '',
                )

        self._seed_control_plane(
            tenant=tenant,
            admin_email=admin_email,
            admin_password=admin_password,
            rotar_password=rotar_password,
            admin_username=opts['admin_username'],
            rol='ADMIN',
            token=result['token'],
            sucursal_codigo=sucursal_codigo,
        )

        # Recien aca el tenant queda enrutable: base creada, migrada y sembrada,
        # y control plane consistente.
        tenant.activo = True
        tenant.save(update_fields=['activo'])

        self.stdout.write(self.style.SUCCESS(
            f'Bootstrap OK: {tenant.tenant_key} ({tenant.db_name}). Tenant activo.'
        ))
        self._reportar_secretos(
            token=result['token'],
            mostrar_token=mostrar_token,
            admin_email=admin_email,
            admin_password=admin_password if password_generada else None,
        )

    def _resolver_password(self, password_explicita, admin_email, tenant_key):
        """
        Devuelve (password, fue_generada).

        Si el admin ya existe y no se pide rotacion, la password devuelta NO se
        aplica (ver `_seed_control_plane`); igual se resuelve una para el caso
        de alta.
        """
        if password_explicita:
            return password_explicita, False

        ya_existe = Identity.objects.using('default').filter(
            email__iexact=admin_email,
        ).exists()
        if ya_existe:
            # No hace falta password: el rerun conserva la vigente.
            return None, False

        # Alta sin `--admin-password`: secreto aleatorio de un solo uso.
        return secrets.token_urlsafe(18), True

    def _reportar_secretos(self, *, token, mostrar_token, admin_email, admin_password):
        if admin_password:
            self.stdout.write(self.style.WARNING(
                'Password inicial generada para %s: %s' % (admin_email, admin_password)
            ))
            self.stdout.write(
                '  Guardala en el gestor de secretos AHORA: no se vuelve a mostrar.'
            )

        if mostrar_token:
            self.stdout.write(self.style.WARNING(f'Token sync: {token}'))
            self.stdout.write(
                '  Se imprimio por --mostrar-token. Si este comando corrio en CI '
                'o en un job, rota el token: el log lo retiene.'
            )
        else:
            self.stdout.write(
                f'Token sync: {token[:6]}...{token[-4:]} '
                f'(usa --mostrar-token para verlo completo)'
            )

    def _ensure_database(self, tenant):
        try:
            exists = database_exists(tenant.db_name)
        except Exception as exc:
            raise CommandError(f'No se pudo verificar la BD {tenant.db_name}: {exc}') from exc

        if exists:
            self.stdout.write(f'BD existente: {tenant.db_name}')
            return

        self.stdout.write(f'Creando BD: {tenant.db_name}')
        try:
            create_database(tenant.db_name)
        except Exception as exc:
            raise CommandError(f'No se pudo crear la BD {tenant.db_name}: {exc}') from exc

    def _seed_tenant(
        self,
        *,
        tenant,
        nombre,
        slug,
        rnc,
        admin_email,
        admin_password,
        rotar_password,
        admin_username,
        sucursal_codigo,
        sucursal_nombre,
        plan_slug,
    ):
        from apps.configuracion.models import ConfiguracionNegocio
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

        # `self_row()` falla si hay mas de una fila, en vez de retitular la
        # de menor PK y dejar el resto colgando (NEG-005).
        negocio = Negocio.self_row()
        if negocio is None:
            negocio = Negocio.objects.create(nombre=nombre, slug=slug, rnc=rnc, activo=True)
        else:
            negocio.nombre = nombre
            negocio.slug = slug
            negocio.rnc = rnc
            negocio.activo = True
            negocio.save(update_fields=['nombre', 'slug', 'rnc', 'activo', 'fecha_modificacion'])

        sucursal, _ = Sucursal.objects.get_or_create(
            codigo=sucursal_codigo,
            defaults={'nombre': sucursal_nombre, 'negocio': negocio, 'activa': True},
        )
        changed = []
        if sucursal.negocio_id != negocio.id:
            sucursal.negocio = negocio
            changed.append('negocio')
        if not sucursal.activa:
            sucursal.activa = True
            changed.append('activa')
        if sucursal.nombre != sucursal_nombre:
            sucursal.nombre = sucursal_nombre
            changed.append('nombre')
        if changed:
            sucursal.save(update_fields=changed)

        ConfiguracionNegocio.objects.get_or_create(
            sucursal=sucursal,
            defaults={'nombre_negocio': nombre, 'rnc': rnc},
        )

        User = get_user_model()
        admin_user, created = User.objects.get_or_create(
            username=admin_username,
            defaults={
                'email': admin_email,
                'first_name': 'Admin',
                'last_name': nombre[:120],
                'rol': 'ADMIN',
                'activo': True,
                'is_staff': True,
                'is_superuser': False,
                'negocio': negocio,
            },
        )
        admin_user.email = admin_email
        admin_user.rol = 'ADMIN'
        admin_user.activo = True
        admin_user.is_staff = True
        admin_user.negocio = negocio
        # La password SOLO se escribe al crear o con rotacion explicita. Un
        # rerun del bootstrap no debe tocar la credencial vigente del dueno.
        if created or rotar_password:
            if not admin_password:
                raise CommandError(
                    'Se pidio rotar la password pero no se recibio ninguna. '
                    'Pasa --admin-password.'
                )
            admin_user.set_password(admin_password)
        admin_user.save()

        bootstrap_rbac(
            NegocioModel=Negocio,
            SucursalModel=Sucursal,
            UsuarioModel=User,
            RolModel=Rol,
            PermisoModel=Permiso,
            AsignacionRolModel=AsignacionRol,
            nombre=nombre,
        )
        from apps.notificaciones.seed import crear_reglas_default
        crear_reglas_default(negocio)

        suscripciones_seed.bootstrap(
            ModuloModel=Modulo,
            PlanModel=Plan,
            NegocioModel=Negocio,
            NegocioModuloModel=NegocioModulo,
            SuscripcionModel=SuscripcionNegocio,
            ConfiguracionModel=ConfiguracionNegocio,
        )
        if plan_slug:
            plan = Plan.objects.filter(slug=plan_slug).first()
            if plan is not None:
                SuscripcionNegocio.objects.update_or_create(
                    negocio=negocio,
                    defaults={'plan': plan, 'activa': True},
                )

        service_username = f'sucursal_service_{sucursal_codigo}'
        service_user, _ = User.objects.get_or_create(
            username=service_username,
            defaults={
                'email': f'{service_username.lower()}@{tenant.tenant_key}.sync.local',
                'first_name': 'Sucursal',
                'last_name': sucursal_codigo,
                'rol': 'CAJERA',
                'activo': True,
                'negocio': negocio,
            },
        )
        service_user.negocio = negocio
        service_user.activo = True
        service_user.set_unusable_password()
        service_user.save()

        if sucursal.usuario_servicio_id != service_user.id:
            sucursal.usuario_servicio = service_user
            sucursal.save(update_fields=['usuario_servicio'])

        token, _ = Token.objects.get_or_create(user=service_user)
        return {'token': token.key}

    def _seed_control_plane(
        self,
        *,
        tenant,
        admin_email,
        admin_password,
        rotar_password,
        admin_username,
        rol,
        token,
        sucursal_codigo,
    ):
        identity, identity_creada = Identity.objects.using('default').get_or_create(
            email=admin_email,
            defaults={'nombre': admin_email, 'activo': True},
        )
        identity.activo = True
        # Misma regla que el usuario operativo: no se pisa una credencial ya
        # establecida salvo rotacion explicita.
        if identity_creada or rotar_password:
            if not admin_password:
                raise CommandError(
                    'Se pidio rotar la password pero no se recibio ninguna. '
                    'Pasa --admin-password.'
                )
            identity.set_password(admin_password)
        identity.save()

        Membership.objects.using('default').update_or_create(
            identity=identity,
            tenant=tenant,
            defaults={'username': admin_username, 'rol': rol, 'activo': True},
        )

        SyncToken.objects.using('default').update_or_create(
            tenant=tenant,
            sucursal_codigo=sucursal_codigo,
            defaults={
                'token_hash': SyncToken.hash_token(token),
                'activo': True,
                'descripcion': f'Sync {tenant.tenant_key}/{sucursal_codigo}',
            },
        )
