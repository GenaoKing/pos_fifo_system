"""
apps/tenancy/tests/test_auditoria_tenancy.py

Regresion de los hallazgos de
`docs/exploracion/AUDITORIA_CODIGO_APPS_TENANCY.md`.

La auditoria reprodujo cada defecto con una bateria adversarial temporal que
despues elimino. Estos tests son esa bateria, hecha permanente: cada uno afirma
la garantia que faltaba, no la implementacion.
"""
from io import StringIO
from unittest.mock import patch

from django.core.checks import run_checks
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.tenancy.models import (
    Identity,
    Membership,
    SesionImpersonacion,
    Tenant,
)


def _desregistrar_alias(prefijo='tnt_'):
    """
    Quita del runtime los alias que el registry creo durante un test.

    `TestCase` recorre `connections` en su teardown para restaurar los metodos
    que bloqueo; un alias dinamico que el runner nunca preparo lo hace fallar
    con `'function' object has no attribute 'wrapped'`. La auditoria tropezo
    con esto mismo al armar su bateria adversarial.
    """
    from django.conf import settings
    from django.db import connections

    for alias in [a for a in list(connections.databases) if a.startswith(prefijo)]:
        connections.databases.pop(alias, None)
        settings.DATABASES.pop(alias, None)
        contenedor = getattr(connections, '_connections', None)
        if contenedor is not None and hasattr(contenedor, alias):
            try:
                delattr(contenedor, alias)
            except AttributeError:
                pass


@override_settings(TENANCY_DB_PER_TENANT_ENABLED=True)
class InvariantesDeIdentidadTests(TestCase):
    """TEN-005 y TEN-011: las claves logicas ahora tienen unicidad real."""

    def test_dos_tenants_no_pueden_compartir_media_prefix(self):
        """
        El escenario reproducido: dos tenants con `media_prefix='shared/'`
        resolvian exactamente `shared/productos/item.jpg`. Un upload
        sobrescribia el blob del otro negocio.
        """
        Tenant.objects.create(
            tenant_key='uno', slug='uno', nombre='Uno',
            db_name='tnt_uno', media_prefix='shared/',
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Tenant.objects.create(
                    tenant_key='dos', slug='dos', nombre='Dos',
                    db_name='tnt_dos', media_prefix='shared/',
                )

    def test_un_media_prefix_vacio_cae_al_default_del_key(self):
        """Un prefijo vacio degradaba las rutas a globales."""
        tenant = Tenant.objects.create(
            tenant_key='vacio', slug='vacio', nombre='Vacio', media_prefix='   ',
        )
        self.assertEqual(tenant.media_prefix, 'vacio/')

    def test_emails_que_solo_difieren_en_mayusculas_colisionan(self):
        """
        El login normaliza a minusculas y busca `iexact`, pero la unicidad de
        PostgreSQL es sensible a mayusculas: la tabla aceptaba las dos y el
        login elegia una u otra segun el orden de las filas.
        """
        Identity.objects.create(email='owner@example.com')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                # `save()` normaliza, asi que se fuerza el insert crudo.
                Identity.objects.bulk_create([Identity(email='Owner@Example.com')])

    def test_identity_normaliza_el_email_al_guardar(self):
        identity = Identity.objects.create(email='  Owner@Example.COM ')
        self.assertEqual(identity.email, 'owner@example.com')

    def test_dos_identidades_no_pueden_reclamar_el_mismo_usuario_operativo(self):
        """
        Dos identities distintas apuntando a `tenant/username=admin`: dos
        credenciales globales actuando como el mismo usuario, y una auditoria
        basada en `Usuario` no puede distinguirlas.
        """
        tenant = Tenant.objects.create(tenant_key='t1', slug='t1', nombre='T1')
        a = Identity.objects.create(email='a@example.com')
        b = Identity.objects.create(email='b@example.com')
        Membership.objects.create(identity=a, tenant=tenant, username='admin')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Membership.objects.create(identity=b, tenant=tenant, username='admin')


@override_settings(TENANCY_DB_PER_TENANT_ENABLED=True)
class RegistryTests(TestCase):
    """TEN-004: identidad de routing inmutable y sin conexiones obsoletas."""

    def tearDown(self):
        _desregistrar_alias()

    def test_el_registry_rechaza_una_instancia_inactiva(self):
        """
        El chequeo de `activo` vivia solo en la rama que busca por key; pasar
        una instancia (lo que hacen los comandos) lo saltaba.
        """
        from apps.tenancy.registry import configure_tenant_database

        tenant = Tenant.objects.create(
            tenant_key='inactivo', slug='inactivo', nombre='Inactivo', activo=False,
        )

        with self.assertRaises(Tenant.DoesNotExist):
            configure_tenant_database(tenant)

    def test_el_aprovisionamiento_si_puede_usar_un_inactivo(self):
        """El bootstrap necesita conectarse antes de publicar el tenant."""
        from apps.tenancy.registry import configure_tenant_database

        tenant = Tenant.objects.create(
            tenant_key='provisionando', slug='prov', nombre='Prov', activo=False,
        )

        resuelto, alias = configure_tenant_database(tenant, permitir_inactivo=True)

        self.assertEqual(resuelto.pk, tenant.pk)
        self.assertEqual(alias, 'tnt_provisionando')

    def test_cambiar_db_name_descarta_la_conexion_cacheada(self):
        """
        La reproduccion de la auditoria: se configuraba `tenant_old`, se
        materializaba su wrapper, se cambiaba `db_name` y se reconfiguraba. El
        diccionario mostraba el nombre nuevo pero el wrapper cacheado conservaba
        el viejo, asi que ese worker seguia escribiendo en la base anterior.
        """
        from django.db import connections

        from apps.tenancy.registry import configure_tenant_database

        tenant = Tenant.objects.create(
            tenant_key='rot', slug='rot', nombre='Rot', db_name='tnt_old',
        )
        _, alias = configure_tenant_database(tenant)

        # Materializa el wrapper (sin conectar).
        wrapper = connections[alias]
        self.assertEqual(wrapper.settings_dict['NAME'], 'tnt_old')

        tenant.db_name = 'tnt_new'
        tenant.save(update_fields=['db_name'])
        configure_tenant_database(tenant)

        # El wrapper viejo quedo descartado: el proximo acceso crea uno nuevo
        # con la configuracion actual.
        self.assertEqual(connections[alias].settings_dict['NAME'], 'tnt_new')

    def test_el_admin_congela_la_identidad_de_routing(self):
        from django.contrib import admin as django_admin

        from apps.tenancy.admin import TenantAdmin

        tenant_admin = TenantAdmin(Tenant, django_admin.site)
        tenant = Tenant.objects.create(tenant_key='x', slug='x', nombre='X')

        # Al crear son editables; despues no.
        self.assertEqual(tenant_admin.get_readonly_fields(None, None), ())
        self.assertEqual(
            set(tenant_admin.get_readonly_fields(None, tenant)),
            set(Tenant.CAMPOS_INMUTABLES),
        )
        self.assertFalse(tenant_admin.has_delete_permission(None, tenant))


@override_settings(TENANCY_DB_PER_TENANT_ENABLED=True)
class RevocacionDeSesionTests(TestCase):
    """
    TEN-001 y TEN-002: revocar el acceso tiene que cortar la sesion.

    Estos son los dos hallazgos mas severos. La reproduccion de la auditoria:
    se emitia un JWT para una membership ADMIN, se eliminaba la membership y se
    bajaba el usuario a CAJERA — y el access token PREVIO seguia autenticando.
    El mismo refresh, ademas, se podia canjear dos veces con 200.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(
            tenant_key='rev', slug='rev', nombre='Rev', db_name='tnt_rev',
        )
        self.identity = Identity.objects.create(email='admin@rev.local')
        self.membership = Membership.objects.create(
            identity=self.identity, tenant=self.tenant, username='admin', rol='ADMIN',
        )

    def tearDown(self):
        _desregistrar_alias()

    def _autorizar(self, *, impersonado=False, username='admin'):
        from apps.tenancy.authentication import _autorizar_tenant

        return _autorizar_tenant(
            identity=self.identity,
            tenant=self.tenant,
            username=username,
            impersonado=impersonado,
        )

    def test_con_membership_activa_autoriza(self):
        self.assertIsNotNone(self._autorizar())

    def test_eliminar_la_membership_corta_el_acceso(self):
        from rest_framework.exceptions import AuthenticationFailed

        self.membership.delete()

        with self.assertRaises(AuthenticationFailed) as ctx:
            self._autorizar()
        self.assertEqual(ctx.exception.detail.code, 'membership_revocada')

    def test_desactivar_la_membership_corta_el_acceso(self):
        from rest_framework.exceptions import AuthenticationFailed

        self.membership.activo = False
        self.membership.save(update_fields=['activo'])

        with self.assertRaises(AuthenticationFailed):
            self._autorizar()

    def test_cambiar_el_username_corta_el_acceso(self):
        """
        Que exista "alguna" membership no alcanza: si el username cambia, el
        token viejo estaria actuando como un usuario operativo distinto.
        """
        from rest_framework.exceptions import AuthenticationFailed

        self.membership.username = 'otro_admin'
        self.membership.save(update_fields=['username'])

        with self.assertRaises(AuthenticationFailed):
            self._autorizar(username='admin')

    def test_la_impersonacion_no_exige_membership_pero_si_identidad_global(self):
        from rest_framework.exceptions import AuthenticationFailed

        self.membership.delete()
        self.identity.is_global = True
        self.identity.save(update_fields=['is_global'])

        # Un operador global sin membership SI puede seguir impersonando...
        self.assertIsNone(self._autorizar(impersonado=True))

        # ...pero si se le retira `is_global`, su sesion impersonada muere.
        self.identity.is_global = False
        self.identity.save(update_fields=['is_global'])
        with self.assertRaises(AuthenticationFailed) as ctx:
            self._autorizar(impersonado=True)
        self.assertEqual(ctx.exception.detail.code, 'impersonacion_revocada')

    def test_el_refresh_aplica_la_misma_regla_que_la_autenticacion(self):
        """
        De nada sirve cortar el access si el refresh puede fabricar otro. Antes
        `/auth/refresh/` era el `TokenRefreshView` generico: solo miraba firma y
        vencimiento.
        """
        from apps.api.auth_views import TenantTokenRefreshSerializer
        from apps.tenancy.authentication import _autorizar_tenant

        self.assertIs(
            TenantTokenRefreshSerializer.validate.__globals__['_autorizar_tenant'],
            _autorizar_tenant,
        )

    def test_la_blacklist_esta_instalada(self):
        """
        Sin `token_blacklist`, `ROTATE_REFRESH_TOKENS` daba apariencia de
        reemplazo pero el refresh anterior seguia siendo canjeable.
        """
        from django.apps import apps as django_apps

        self.assertTrue(
            django_apps.is_installed('rest_framework_simplejwt.token_blacklist')
        )

    def test_la_blacklist_es_dual_home_como_los_usuarios(self):
        """
        `OutstandingToken` tiene una FK a `usuarios`, asi que tiene que resolver
        a la MISMA base que el usuario.

        Este test reemplaza a uno anterior que exigia lo contrario --que la
        blacklist viviera solo en el control plane-- con el argumento de que la
        sesion del portal es global a la identidad. Ese reparto **tumbo el login
        de produccion** el 2026-08-22: al autenticar un usuario de tenant,
        `RefreshToken.for_user()` intentaba crear el OutstandingToken en
        `default` con FK a un Usuario cargado desde `tnt_*`, y el router lo
        rechazaba con "the current database router prevents this relation".
        """
        from apps.tenancy.router import (
            DEFAULT_ONLY_APPS,
            DUAL_HOME_APPS,
            TenantDatabaseRouter,
        )

        self.assertIn('token_blacklist', DUAL_HOME_APPS)
        self.assertNotIn('token_blacklist', DEFAULT_ONLY_APPS)

        router = TenantDatabaseRouter()
        # Tiene que poder migrarse en AMBOS lados: en el control plane para los
        # usuarios que viven ahi, y en cada tenant para los suyos.
        self.assertTrue(router.allow_migrate('default', 'token_blacklist'))
        self.assertTrue(router.allow_migrate('tnt_rev', 'token_blacklist'))

    def test_la_blacklist_resuelve_a_la_misma_base_que_el_usuario(self):
        """La garantia de fondo: la FK nunca cruza bases."""
        from django.contrib.auth import get_user_model
        from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

        from apps.tenancy.router import TenantDatabaseRouter

        router = TenantDatabaseRouter()
        self.assertEqual(
            router.db_for_write(OutstandingToken),
            router.db_for_write(get_user_model()),
            'OutstandingToken y Usuario deben resolver a la misma base',
        )

    def test_cloud_rota_y_lista_negra_el_refresh(self):
        """
        `settings_cloud` exige variables de entorno para importarse, asi que se
        verifica sobre la fuente: lo que importa es que el valor quede fijado,
        no como se cargue.
        """
        import inspect
        import pathlib

        fuente = pathlib.Path(
            inspect.getfile(self.__class__)
        ).parents[3].joinpath('config', 'settings_cloud.py').read_text(encoding='utf-8')

        self.assertIn("'ROTATE_REFRESH_TOKENS': True", fuente)
        self.assertIn("'BLACKLIST_AFTER_ROTATION': True", fuente)
        self.assertNotIn("'BLACKLIST_AFTER_ROTATION': False", fuente)


class LoginRateLimitTests(TestCase):
    """TEN-009: quince intentos invalidos no producian ningun 429."""

    def test_el_login_declara_sus_throttles(self):
        from apps.api.auth_views import PortalTokenObtainPairView
        from apps.api.throttling import LoginRafagaThrottle, LoginSostenidoThrottle

        clases = PortalTokenObtainPairView.throttle_classes
        self.assertIn(LoginRafagaThrottle, clases)
        self.assertIn(LoginSostenidoThrottle, clases)

    def test_los_scopes_tienen_tasa_configurada(self):
        from django.conf import settings

        tasas = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']
        self.assertIn('login', tasas)
        self.assertIn('login_sostenido', tasas)

    def test_la_clave_combina_ip_y_email(self):
        """
        Solo por IP, un NAT corporativo bloquearia a todos sus usuarios; solo
        por email, cualquiera podria bloquear a un usuario conocido.
        """
        from rest_framework.test import APIRequestFactory

        from apps.api.throttling import LoginRafagaThrottle

        throttle = LoginRafagaThrottle()
        peticion = APIRequestFactory().post('/api/v1/auth/login/')
        peticion.data = {'email': 'Alguien@Example.com'}

        clave = throttle.get_cache_key(peticion, None)

        self.assertIn('alguien@example.com', clave)

        # Otro email desde la misma IP tiene su propio presupuesto.
        peticion.data = {'email': 'otro@example.com'}
        self.assertNotEqual(throttle.get_cache_key(peticion, None), clave)


class SystemChecksAislamientoTests(TestCase):
    """TEN-013: el escape de aislamiento no puede quedar abierto en silencio."""

    @override_settings(
        TENANCY_DB_PER_TENANT_ENABLED=True,
        TENANCY_ALLOW_UNSCOPED_OPERATIONS=True,
    )
    def test_el_escape_con_tenancy_activa_es_critico(self):
        from apps.tenancy.checks import escape_no_permitido_en_cloud

        problemas = escape_no_permitido_en_cloud(None)

        self.assertEqual(len(problemas), 1)
        self.assertEqual(problemas[0].id, 'tenancy.C001')

    @override_settings(
        TENANCY_DB_PER_TENANT_ENABLED=True,
        TENANCY_ALLOW_UNSCOPED_OPERATIONS=False,
    )
    def test_sin_escape_no_hay_problema(self):
        from apps.tenancy.checks import escape_no_permitido_en_cloud

        self.assertEqual(escape_no_permitido_en_cloud(None), [])

    @override_settings(
        TENANCY_DB_PER_TENANT_ENABLED=False,
        TENANCY_ALLOW_UNSCOPED_OPERATIONS=True,
    )
    def test_sin_tenancy_el_escape_es_legitimo(self):
        """En desarrollo mono-base el escape es la conducta normal."""
        from apps.tenancy.checks import escape_no_permitido_en_cloud

        self.assertEqual(escape_no_permitido_en_cloud(None), [])


@override_settings(TENANCY_DB_PER_TENANT_ENABLED=True)
class MediaFailLoudTests(TestCase):
    """TEN-014: un fallo de infraestructura no puede degradarse en silencio."""

    def tearDown(self):
        from apps.tenancy.context import clear_current_tenant

        clear_current_tenant()
        _desregistrar_alias()

    def test_sin_tenant_en_el_control_plane_falla(self):
        from apps.tenancy.context import TenantContextError, tenant_context
        from apps.tenancy.media import tenant_media_prefix

        tenant = Tenant.objects.create(tenant_key='fantasma', slug='f', nombre='F')

        with tenant_context(tenant):
            # El tenant existe: resuelve normal.
            self.assertEqual(tenant_media_prefix(), 'fantasma/')

            # Se da de baja: ya no es resoluble y NO se degrada a un prefijo
            # derivado del key.
            Tenant.objects.filter(pk=tenant.pk).update(activo=False)
            with self.assertRaises(TenantContextError):
                tenant_media_prefix()


class WithTenantTests(TestCase):
    """TEN-015: el wrapper acepta opciones nombradas."""

    def test_reenvia_opciones_despues_de_doble_guion(self):
        """
        Antes `with_tenant --tenant X check --deploy` moria en argparse con
        "unrecognized arguments: --deploy", ANTES de validar el tenant.
        """
        Tenant.objects.create(tenant_key='wt', slug='wt', nombre='WT')

        recibidos = {}

        def fake_call_command(nombre, *args, **kwargs):
            recibidos['nombre'] = nombre
            recibidos['args'] = args

        with patch(
            'apps.tenancy.management.commands.with_tenant.call_command',
            fake_call_command,
        ), patch(
            'apps.tenancy.management.commands.with_tenant.tenant_context',
        ):
            call_command('with_tenant', '--tenant', 'wt', 'check', '--', '--deploy')

        self.assertEqual(recibidos['nombre'], 'check')
        self.assertIn('--deploy', recibidos['args'])


class BackupTenantTests(TestCase):
    """TEN-010: el comando ya no simula un backup que no hace."""

    def test_solo_comando_avisa_que_no_hay_backup(self):
        Tenant.objects.create(tenant_key='bk', slug='bk', nombre='BK', db_name='tnt_bk')

        salida = StringIO()
        call_command('backup_tenant', '--tenant', 'bk', '--solo-comando', stdout=salida)
        texto = salida.getvalue()

        self.assertIn('pg_dump', texto)
        self.assertIn('No se ejecuto nada', texto)
        # La password nunca sale en claro.
        self.assertNotIn('Prueba123', texto)

    def test_sin_pg_dump_en_el_path_falla_en_vez_de_reportar_exito(self):
        Tenant.objects.create(tenant_key='bk2', slug='bk2', nombre='BK2', db_name='tnt_bk2')

        with patch(
            'apps.tenancy.management.commands.backup_tenant.shutil.which',
            return_value=None,
        ):
            with self.assertRaisesMessage(CommandError, 'pg_dump no esta en el PATH'):
                call_command('backup_tenant', '--tenant', 'bk2')

    def test_un_dump_vacio_es_error(self):
        """Un exit 0 de pg_dump sin artefacto NO es un backup."""
        import subprocess

        Tenant.objects.create(tenant_key='bk3', slug='bk3', nombre='BK3', db_name='tnt_bk3')

        exito = subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr='')

        with patch(
            'apps.tenancy.management.commands.backup_tenant.shutil.which',
            return_value='pg_dump',
        ), patch(
            'apps.tenancy.management.commands.backup_tenant.subprocess.run',
            return_value=exito,
        ):
            with self.assertRaisesMessage(CommandError, 'NO hay backup'):
                call_command('backup_tenant', '--tenant', 'bk3')


class ImpersonacionAuditadaTests(TestCase):
    """TEN-008: la impersonacion deja un rastro durable con el actor global."""

    def test_el_modelo_registra_actor_objetivo_y_motivo(self):
        tenant = Tenant.objects.create(tenant_key='imp', slug='imp', nombre='Imp')
        identity = Identity.objects.create(email='soporte@example.com', is_global=True)

        sesion = SesionImpersonacion.objects.create(
            identity=identity,
            tenant=tenant,
            username_objetivo='admin',
            motivo='TICKET-123',
            ip_address='10.0.0.1',
        )

        self.assertIn('soporte@example.com', str(sesion))
        self.assertIn('imp/admin', str(sesion))
        self.assertIsNone(sesion.cierre)


@override_settings(TENANCY_DB_PER_TENANT_ENABLED=True)
class BootstrapTenantTests(TestCase):
    """TEN-003 y TEN-006: sin credenciales por defecto, publicado al final."""

    def test_dry_run_no_escribe_ni_revela_secretos(self):
        salida = StringIO()
        call_command(
            'bootstrap_tenant', tenant='nuevo', nombre='Nuevo',
            dry_run=True, stdout=salida,
        )

        self.assertIn('DRY-RUN', salida.getvalue())
        self.assertFalse(Tenant.objects.filter(tenant_key='nuevo').exists())

    def test_el_password_por_defecto_ya_no_existe(self):
        """
        El default literal estaba publicado en runbooks: una ejecucion
        accidental podia reemplazar una password fuerte por la conocida.
        """
        import inspect

        from apps.tenancy.management.commands import bootstrap_tenant

        fuente = inspect.getsource(bootstrap_tenant)
        self.assertNotIn('Admin123!', fuente)

    def test_normalizar_import_tampoco_tiene_default(self):
        import inspect

        from apps.tenancy.management.commands import normalizar_import_tenant

        fuente = inspect.getsource(normalizar_import_tenant)
        self.assertNotIn("default='Admin123!'", fuente)
