import secrets

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify
from rest_framework.authtoken.models import Token

from apps.tenancy.context import force_tenancy, tenant_context
from apps.tenancy.db import create_database, database_exists
from apps.tenancy.models import Identity, Membership, SyncToken, Tenant
from apps.tenancy.registry import configure_tenant_database
from apps.tenancy.services import (
    marcar_estado_provisioning,
    persistir_plan_slug_validado,
    preparar_tenant_provisioning,
)
from apps.suscripciones.seed import PlanDesconocido, validar_plan_slug


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
            help='Password inicial del Usuario operativo local. Si se omite '
                 'al crearlo, se genera uno aleatorio de un solo uso.',
        )
        parser.add_argument(
            '--identity-password',
            help='Password independiente de la Identity del portal. Si se '
                 'omite al crearla, se genera otro secreto de un solo uso.',
        )
        parser.add_argument(
            '--rotar-password',
            action='store_true',
            help='Compatibilidad: exige ambos secretos y rota ambas puertas. '
                 'Preferir los flags de rotacion explicitos.',
        )
        parser.add_argument(
            '--rotar-admin-password',
            action='store_true',
            help='Rota solo la credencial del Usuario operativo local.',
        )
        parser.add_argument(
            '--rotar-identity-password',
            action='store_true',
            help='Rota solo la credencial Identity usada por el portal.',
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

        identity_existente = Identity.objects.using('default').filter(
            email__iexact=admin_email,
        ).exists()
        rotar_admin_password = opts['rotar_admin_password']
        rotar_identity_password = opts['rotar_identity_password']
        if opts['rotar_password']:
            if not opts.get('admin_password') or not opts.get('identity_password'):
                raise CommandError(
                    '--rotar-password exige --admin-password e '
                    '--identity-password; las credenciales ya no se sincronizan.'
                )
            rotar_admin_password = True
            rotar_identity_password = True
        if rotar_admin_password and not opts.get('admin_password'):
            raise CommandError('--rotar-admin-password requiere --admin-password.')
        if rotar_identity_password and not opts.get('identity_password'):
            raise CommandError(
                '--rotar-identity-password requiere --identity-password.'
            )

        # Usuario local e Identity portal son puertas independientes. Para un
        # alta se generan secretos distintos; un rerun no reemplaza ninguno.
        admin_password, admin_password_generada = self._resolver_password(
            opts.get('admin_password'), existe=False,
        )
        identity_password, identity_password_generada = self._resolver_password(
            opts.get('identity_password'), existe=identity_existente,
        )
        if (
            admin_password and identity_password
            and admin_password == identity_password
        ):
            raise CommandError(
                'La password local y la Identity del portal deben ser distintas.'
            )
        mostrar_token = opts['mostrar_token']
        opts['admin_username'] = opts['admin_username'].strip().casefold()
        sucursal_codigo = opts['sucursal_codigo'].strip().upper()
        sucursal_nombre = opts.get('sucursal_nombre') or f'{nombre} - Principal'
        plan_slug = opts.get('plan') or ''
        dry_run = opts['dry_run']

        if admin_password:
            try:
                validate_password(admin_password)
            except ValidationError as exc:
                raise CommandError('; '.join(exc.messages)) from exc
        if identity_password and (rotar_identity_password or not identity_existente):
            try:
                validate_password(identity_password)
            except ValidationError as exc:
                raise CommandError('; '.join(exc.messages)) from exc

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

        if existing_tenant is not None:
            if explicit_slug and slug != existing_tenant.slug:
                raise CommandError(
                    'El slug de un tenant existente es inmutable. Use una '
                    'migracion controlada, no bootstrap.'
                )
            if existing_tenant.db_name != f'tnt_{tenant_key}':
                raise CommandError('db_name existente no coincide con tenant_key.')
            if existing_tenant.media_prefix != f'{tenant_key}/':
                raise CommandError('media_prefix existente no coincide con tenant_key.')

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
        tenant, _tenant_creado, activo_previo = preparar_tenant_provisioning(
            tenant_key=tenant_key,
            slug=slug,
            nombre=nombre,
            rnc=opts.get('rnc', ''),
        )

        try:
            tenant = marcar_estado_provisioning(
                tenant, Tenant.EstadoProvisioning.PENDING,
                incrementar_intento=True,
            )
            self._ensure_database(tenant)
            tenant = marcar_estado_provisioning(tenant, Tenant.EstadoProvisioning.DB_READY)
            # El tenant esta inactivo a proposito mientras se aprovisiona.
            configure_tenant_database(tenant, permitir_inactivo=True)

            if not opts['skip_migrate']:
                call_command(
                    'migrate_tenants', tenant=tenant.tenant_key,
                    noinput=True, incluir_inactivos=True,
                )
            tenant = marcar_estado_provisioning(tenant, Tenant.EstadoProvisioning.SCHEMA_READY)

            with force_tenancy(True):
                with tenant_context(tenant, permitir_inactivo=True):
                    alias = f'tnt_{tenant.tenant_key}'
                    try:
                        validar_plan_slug(plan_slug, using=alias)
                    except PlanDesconocido as exc:
                        raise CommandError(str(exc)) from exc
                    with transaction.atomic(using=alias):
                        result = self._seed_tenant(
                            tenant=tenant,
                            nombre=nombre,
                            slug=slug,
                            rnc=opts.get('rnc', ''),
                            admin_email=admin_email,
                            admin_password=admin_password,
                            rotar_admin_password=rotar_admin_password,
                            admin_username=opts['admin_username'],
                            sucursal_codigo=sucursal_codigo,
                            sucursal_nombre=sucursal_nombre,
                            plan_slug=plan_slug,
                        )
            # La suscripcion ya se escribio en la transaccion tenant. Solo
            # despues se publica el slug validado en el control plane; un
            # plan desconocido no llega a escribir ninguna de las dos cosas.
            tenant = persistir_plan_slug_validado(tenant, plan_slug)
            tenant = marcar_estado_provisioning(tenant, Tenant.EstadoProvisioning.TENANT_READY)

            with transaction.atomic(using='default'):
                self._seed_control_plane(
                    tenant=tenant,
                    admin_email=admin_email,
                    identity_password=identity_password,
                    rotar_identity_password=rotar_identity_password,
                    admin_username=result['admin_username'],
                    rol='ADMIN',
                    token=result['token'],
                    sucursal_codigo=sucursal_codigo,
                )
            tenant = marcar_estado_provisioning(tenant, Tenant.EstadoProvisioning.CONTROL_READY)

            # Recien aca un tenant nuevo queda enrutable. Un tenant que ya
            # estaba activo nunca se dio de baja durante la revalidacion.
            tenant = marcar_estado_provisioning(
                tenant, Tenant.EstadoProvisioning.ACTIVE, activo=True,
            )
        except Exception as exc:
            # No existe atomicidad falsa entre control plane y BD tenant. Cada
            # fase es idempotente, el estado dice donde fallo y el mismo
            # comando reanuda verificando de nuevo las fases ya ejecutadas.
            try:
                marcar_estado_provisioning(
                    tenant,
                    Tenant.EstadoProvisioning.FAILED,
                    activo=activo_previo,
                    error=exc,
                )
            except Exception:
                pass
            raise

        self.stdout.write(self.style.SUCCESS(
            f'Bootstrap OK: {tenant.tenant_key} ({tenant.db_name}). Tenant activo.'
        ))
        self._reportar_secretos(
            token=result['token'],
            mostrar_token=mostrar_token,
            admin_email=admin_email,
            admin_password=(
                admin_password
                if admin_password_generada and result['admin_password_applied']
                else None
            ),
            identity_password=(
                identity_password if identity_password_generada else None
            ),
        )

    def _resolver_password(self, password_explicita, *, existe):
        """Devuelve ``(secreto, fue_generado)`` sin reutilizar otra puerta."""
        if password_explicita:
            return password_explicita, False
        if existe:
            return None, False
        return secrets.token_urlsafe(18), True

    def _reportar_secretos(
        self, *, token, mostrar_token, admin_email, admin_password,
        identity_password,
    ):
        if admin_password:
            self.stdout.write(self.style.WARNING(
                'Password POS local generada para %s: %s'
                % (admin_email, admin_password)
            ))
        if identity_password:
            self.stdout.write(self.style.WARNING(
                'Password portal Identity generada para %s: %s'
                % (admin_email, identity_password)
            ))
        if admin_password or identity_password:
            self.stdout.write(
                '  Guardalas por separado en el gestor de secretos AHORA: '
                'no se vuelven a mostrar.'
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
        rotar_admin_password,
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
            if negocio.slug != slug:
                raise CommandError(
                    f'El self-row usa slug estable "{negocio.slug}" y el control '
                    f'plane solicita "{slug}". No se fusionan aproximadamente: '
                    'resolver la identidad antes de reanudar.'
                )
            negocio.nombre = nombre
            negocio.rnc = rnc
            negocio.activo = True
            negocio.save(update_fields=['nombre', 'rnc', 'activo', 'fecha_modificacion'])

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

        config, _ = ConfiguracionNegocio.objects.get_or_create(
            sucursal=sucursal,
            defaults={'nombre_negocio': nombre, 'rnc': rnc},
        )
        campos_config = []
        if config.nombre_negocio != nombre:
            config.nombre_negocio = nombre
            campos_config.append('nombre_negocio')
        if config.rnc != negocio.rnc:
            config.rnc = negocio.rnc
            campos_config.append('rnc')
        if campos_config:
            config.save(update_fields=campos_config)

        User = get_user_model()
        coincidencias = list(User.objects.filter(username__iexact=admin_username)[:2])
        if len(coincidencias) > 1:
            raise CommandError('Hay usernames equivalentes por mayusculas; resolver primero.')
        admin_user = coincidencias[0] if coincidencias else None
        created = admin_user is None
        if created:
            if not admin_password:
                raise CommandError('El primer usuario requiere --admin-password.')
            admin_user = User.objects.create_human_user(
                username=admin_username,
                email=admin_email,
                password=admin_password,
                first_name='Admin',
                last_name=nombre[:120],
                rol='ADMIN',
                activo=True,
                is_staff=True,
                is_superuser=False,
                negocio=negocio,
            )
        admin_user.email = admin_email
        admin_user.rol = 'ADMIN'
        admin_user.activo = True
        admin_user.is_staff = True
        admin_user.negocio = negocio
        # La password SOLO se escribe al crear o con rotacion explicita. Un
        # rerun del bootstrap no debe tocar la credencial vigente del dueno.
        if created or rotar_admin_password:
            if not admin_password:
                raise CommandError(
                    'Se pidio rotar la password pero no se recibio ninguna. '
                    'Pasa --admin-password.'
                )
            admin_user.set_password(admin_password)
        admin_user.full_clean()
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
            try:
                plan = Plan.objects.get(slug=plan_slug)
            except Plan.DoesNotExist as exc:
                raise CommandError(
                    f'El plan validado "{plan_slug}" ya no existe en la BD tenant.'
                ) from exc
            SuscripcionNegocio.objects.update_or_create(
                negocio=negocio,
                defaults={'plan': plan, 'activa': True},
            )

        service_username = f'sucursal_service_{sucursal_codigo}'
        service_user = User.objects.filter(username__iexact=service_username).first()
        if service_user is None:
            service_user = User.objects.create_service_user(
                username=service_username,
                email=f'{service_username.lower()}@{tenant.tenant_key}.sync.local',
                first_name='Sucursal',
                last_name=sucursal_codigo,
                rol='CAJERA',
                activo=True,
                negocio=negocio,
            )
        service_user.negocio = negocio
        service_user.activo = True
        service_user.set_unusable_password()
        service_user.save()

        if sucursal.usuario_servicio_id != service_user.id:
            sucursal.usuario_servicio = service_user
            sucursal.save(update_fields=['usuario_servicio'])

        token, _ = Token.objects.get_or_create(user=service_user)
        return {
            'token': token.key,
            'admin_username': admin_user.username,
            'admin_password_applied': created or rotar_admin_password,
        }

    def _seed_control_plane(
        self,
        *,
        tenant,
        admin_email,
        identity_password,
        rotar_identity_password,
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
        if identity_creada or rotar_identity_password:
            if not identity_password:
                raise CommandError(
                    'La Identity requiere su password independiente. '
                    'Pasa --identity-password.'
                )
            identity.set_password(identity_password)
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
