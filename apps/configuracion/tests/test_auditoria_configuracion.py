"""
apps/configuracion/tests/test_auditoria_configuracion.py

Regresion de los hallazgos de
`docs/exploracion/AUDITORIA_CODIGO_APPS_CONFIGURACION.md`.
"""
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings

from apps.configuracion.models import ConfiguracionNegocio
from apps.configuracion.utils import (
    ConfiguracionNoResuelta,
    cache_key_config,
    get_config,
    invalidar_config,
)
from apps.permisos import testing as permisos_testing
from apps.sucursales.models import Sucursal
from apps.tenancy.context import force_tenancy

User = get_user_model()


class ConfiguracionTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.negocio = permisos_testing.crear_negocio('Negocio CFG')
        self.suc_a = Sucursal.objects.create(
            codigo='CFG-A', nombre='Tienda A', activa=True, negocio=self.negocio,
        )
        self.suc_b = Sucursal.objects.create(
            codigo='CFG-B', nombre='Tienda B', activa=True, negocio=self.negocio,
        )
        self.config_a = ConfiguracionNegocio.objects.create(
            sucursal=self.suc_a, nombre_negocio='Negocio A', rnc='111',
        )
        self.config_b = ConfiguracionNegocio.objects.create(
            sucursal=self.suc_b, nombre_negocio='Negocio B', rnc='222',
        )

    def tearDown(self):
        cache.clear()

    def _usuario(self, username, permisos=(), rol='CAJERA', **extra):
        user = User.objects.create_user(
            username=username, email=f'{username}@test.local',
            password='Prueba123', rol=rol, activo=True, **extra,
        )
        permisos_testing.habilitar_cajero(user, permisos=list(permisos))
        return user


class ResolucionEstrictaTests(ConfiguracionTestCase):
    """CFG-002: un codigo que no resuelve no devuelve la config de otra tienda."""

    def test_un_codigo_inexistente_falla_en_vez_de_devolver_otra(self):
        """
        La reproduccion: con configuraciones A y B existentes y
        `SUCURSAL_CODIGO='NO-EXISTE'`, `get_config()` devolvia la de A. Un typo
        no detenia la caja: la hacia operar con la identidad fiscal, los pagos y
        los modulos de una sucursal arbitraria.
        """
        with self.settings(SUCURSAL_CODIGO='NO-EXISTE'):
            cache.clear()
            with self.assertRaises(ConfiguracionNoResuelta) as ctx:
                get_config()

        self.assertIn('NO-EXISTE', str(ctx.exception))

    def test_un_codigo_valido_devuelve_la_suya(self):
        with self.settings(SUCURSAL_CODIGO='CFG-B'):
            cache.clear()
            config = get_config()

        self.assertEqual(config.pk, self.config_b.pk)

    def test_cada_sucursal_recibe_la_suya(self):
        with self.settings(SUCURSAL_CODIGO='CFG-A'):
            cache.clear()
            self.assertEqual(get_config().rnc, '111')

        with self.settings(SUCURSAL_CODIGO='CFG-B'):
            cache.clear()
            self.assertEqual(get_config().rnc, '222')


class AislamientoDeCacheTests(ConfiguracionTestCase):
    """CFG-001 y CFG-005."""

    def test_la_clave_lleva_el_namespace_del_tenant(self):
        """
        Los codigos de sucursal son LOCALES a cada base tenant, y `SD-001` es el
        habitual: dos negocios lo comparten legitimamente. La clave era
        `config_negocio_<codigo>` y nada mas.
        """
        with self.settings(SUCURSAL_CODIGO='SD-001'):
            clave_local = cache_key_config()

        self.assertIn('local', clave_local)
        self.assertIn('SD-001', clave_local)

    def test_dos_tenants_con_el_mismo_codigo_no_comparten_clave(self):
        """
        La reproduccion: se cacheo la config del tenant A para `SD-001`; al
        pasar al tenant B —tambien `SD-001`— la segunda llamada devolvio la fila
        de A sin siquiera consultar la base de B.
        """
        from apps.tenancy.context import reset_current_tenant, set_current_tenant

        claves = set()
        for key in ('royalplast', 'skperformance'):
            with force_tenancy(True):
                # No hace falta crear el Tenant: lo que decide la clave es el
                # `tenant_key` en contexto, y crear filas registraria aliases
                # dinamicos que despues rompen el teardown.
                tokens = set_current_tenant(key, 'default')
                try:
                    with self.settings(SUCURSAL_CODIGO='SD-001'):
                        claves.add(cache_key_config())
                finally:
                    reset_current_tenant(tokens)

        self.assertEqual(len(claves), 2)

    def test_sin_tenant_activo_bajo_tenancy_falla_fuerte(self):
        from apps.tenancy.context import TenantContextError

        with force_tenancy(True):
            with self.settings(SUCURSAL_CODIGO='CFG-A'):
                with self.assertRaises(TenantContextError):
                    cache_key_config()

    def test_el_cache_ya_no_es_eterno(self):
        """
        CFG-005: se guardaba con `timeout=None`. Un `QuerySet.update()` no pasa
        por `save()`, asi que el valor viejo quedaba operando indefinidamente —
        y dos replicas podian discrepar sobre si se aceptan pagos en efectivo.
        """
        import apps.configuracion.utils as utils

        self.assertIsNotNone(utils.TTL_CACHE_LOCAL)
        self.assertLessEqual(utils.TTL_CACHE_LOCAL, 60)

        import inspect

        fuente = inspect.getsource(utils.get_config)
        self.assertNotIn('timeout=None', fuente)

    def test_guardar_invalida_con_la_misma_clave_que_lee(self):
        with self.settings(SUCURSAL_CODIGO='CFG-A'):
            cache.clear()
            self.assertEqual(get_config().nombre_negocio, 'Negocio A')

            self.config_a.nombre_negocio = 'Renombrado'
            self.config_a.save()

            self.assertEqual(get_config().nombre_negocio, 'Renombrado')

    def test_invalidar_config_es_publico(self):
        with self.settings(SUCURSAL_CODIGO='CFG-A'):
            cache.clear()
            get_config()
            self.assertIsNotNone(cache.get(cache_key_config()))

            invalidar_config()

            self.assertIsNone(cache.get(cache_key_config()))


class AdminGateadoPorRbacTests(ConfiguracionTestCase):
    """CFG-003: Admin deja de ser una autorizacion paralela."""

    def _admin(self):
        from django.contrib import admin as django_admin

        from apps.configuracion.admin import ConfiguracionNegocioAdmin

        return ConfiguracionNegocioAdmin(
            ConfiguracionNegocio, django_admin.site,
        )

    def _request(self, user):
        peticion = RequestFactory().get('/admin/')
        peticion.user = user
        return peticion

    def test_un_staff_sin_rbac_no_entra(self):
        """
        La reproduccion: un usuario staff con `change_configuracionnegocio`,
        sin `configuracion.administrar`, abrio el changelist con 200 y vio las
        configuraciones de A y B.
        """
        from django.contrib.auth.models import Permission

        staff = self._usuario(
            'staff_django', permisos=['ventas.crear'], is_staff=True,
        )
        staff.user_permissions.add(
            *Permission.objects.filter(
                codename__in=(
                    'view_configuracionnegocio', 'change_configuracionnegocio',
                ),
            )
        )
        staff = User.objects.get(pk=staff.pk)  # refresca el cache de permisos

        instancia = self._admin()
        peticion = self._request(staff)

        self.assertFalse(instancia.has_view_permission(peticion))
        self.assertFalse(instancia.has_change_permission(peticion))
        self.assertFalse(instancia.has_module_permission(peticion))

    def test_con_el_permiso_rbac_si_entra(self):
        autorizado = self._usuario(
            'con_rbac', permisos=['configuracion.administrar'],
            is_staff=True, is_superuser=True,
        )
        autorizado = User.objects.get(pk=autorizado.pk)

        instancia = self._admin()
        peticion = self._request(autorizado)

        self.assertTrue(instancia.has_view_permission(peticion))

    def test_el_queryset_se_acota_a_la_sucursal_del_alcance(self):
        """Un administrador de A no lista la configuracion de B."""
        # Staff con los permisos Django explicitos, NO superusuario: un
        # superusuario tiene alcance RBAC global y el test no probaria nada.
        from django.contrib.auth.models import Permission

        acotado = User.objects.create_user(
            username='admin_a', email='admin_a@test.local', password='x',
            rol='CAJERA', activo=True, is_staff=True,
        )
        acotado.user_permissions.add(
            *Permission.objects.filter(
                codename__in=(
                    'view_configuracionnegocio', 'change_configuracionnegocio',
                ),
            )
        )
        permisos_testing.habilitar_cajero(
            acotado, permisos=['configuracion.administrar'], sucursal=self.suc_a,
        )
        acotado = User.objects.get(pk=acotado.pk)

        visibles = self._admin().get_queryset(self._request(acotado))

        self.assertIn(self.config_a, visibles)
        self.assertNotIn(self.config_b, visibles)

    def test_nunca_se_borra_configuracion(self):
        root = self._usuario('root_cfg', rol='SYSADMIN', is_staff=True,
                             is_superuser=True)

        self.assertFalse(
            self._admin().has_delete_permission(self._request(root))
        )


class SecretosEnDryRunTests(ConfiguracionTestCase):
    """CFG-004: el dry-run no imprime credenciales."""

    def _origen(self, contenido):
        import tempfile
        import pathlib

        ruta = pathlib.Path(tempfile.mkdtemp()) / 'env_cliente.bat'
        ruta.write_text(contenido, encoding='utf-8')
        return str(ruta)

    def test_una_password_no_aparece_en_la_salida(self):
        """
        La reproduccion: un `DB_PASSWORD` de prueba aparecio literalmente en la
        salida capturada del dry-run. Consolas remotas, tickets y logs de CI
        conservan eso.
        """
        origen = self._origen(
            'set DB_NAME=pos_fifo\n'
            'set DB_PASSWORD=SuperSecreta123\n'
            'set DJANGO_SECRET_KEY=abcdefghijklmnop\n'
        )
        salida = StringIO()

        call_command('migrar_env_cliente', '--origen', origen, '--dry-run',
                     stdout=salida)

        texto = salida.getvalue()
        self.assertNotIn('SuperSecreta123', texto)
        self.assertNotIn('abcdefghijklmnop', texto)

    def test_los_valores_no_sensibles_si_se_ven(self):
        origen = self._origen(
            'set DB_NAME=pos_fifo\nset DB_PASSWORD=SuperSecreta123\n'
        )
        salida = StringIO()

        call_command('migrar_env_cliente', '--origen', origen, '--dry-run',
                     stdout=salida)

        self.assertIn('pos_fifo', salida.getvalue())

    def test_el_enmascarado_no_deja_reutilizar_un_secreto_corto(self):
        from apps.configuracion.management.commands.migrar_env_cliente import (
            Command,
        )

        comando = Command()
        self.assertNotIn('abc', comando._enmascarar('abc123'))

    def test_se_decide_por_el_nombre_de_la_variable(self):
        from apps.configuracion.management.commands.migrar_env_cliente import (
            Command,
        )

        comando = Command()
        for nombre in ('DB_PASSWORD', 'DJANGO_SECRET_KEY', 'API_TOKEN',
                       'ECF_CERT_PIN'):
            with self.subTest(nombre=nombre):
                self.assertTrue(comando._es_sensible(nombre))

        self.assertFalse(comando._es_sensible('DB_NAME'))
