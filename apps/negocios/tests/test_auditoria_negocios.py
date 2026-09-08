"""
apps/negocios/tests/test_auditoria_negocios.py

Regresion de los hallazgos de `docs/exploracion/AUDITORIA_CODIGO_APPS_NEGOCIOS.md`.

La app no tenia pruebas propias (NEG-015); este modulo es el arranque.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase

from apps.negocios.models import Negocio, NegocioAmbiguo
from apps.negocios.utils import (
    Resolucion,
    es_principal_global,
    negocio_actual,
    resolver_negocio,
)
from apps.sucursales.models import Sucursal
from apps.tenancy.context import force_tenancy

User = get_user_model()


class NegociosTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.negocio_a = Negocio.objects.create(nombre='Negocio A', slug='negocio-a')
        self.negocio_b = Negocio.objects.create(nombre='Negocio B', slug='negocio-b')

    def tearDown(self):
        cache.clear()

    def _usuario(self, username, rol='CAJERA', negocio=None, **extra):
        return User.objects.create_user(
            username=username, email=f'{username}@test.local', password='x',
            rol=rol, activo=True, negocio=negocio, **extra,
        )

    def _request(self, user, **params):
        peticion = self.factory.get('/', params)
        peticion.user = user
        return peticion


class ResolucionTipadaTests(NegociosTestCase):
    """NEG-001: `None` ya no significa tres cosas distintas."""

    def test_un_usuario_con_negocio_resuelve_su_tenant(self):
        usuario = self._usuario('propio', negocio=self.negocio_a)

        resolucion = resolver_negocio(self._request(usuario))

        self.assertEqual(resolucion.estado, Resolucion.TENANT)
        self.assertEqual(resolucion.negocio, self.negocio_a)

    def test_un_admin_huerfano_no_obtiene_scope_global(self):
        """
        La reproduccion de la auditoria: una cuenta ADMIN activa, no staff, no
        superusuario y con `negocio_id=NULL` recibia las sucursales de los dos
        negocios de prueba.
        """
        huerfano = self._usuario('admin_huerfano', rol='ADMIN')

        resolucion = resolver_negocio(self._request(huerfano))

        self.assertEqual(resolucion.estado, Resolucion.SIN_ACCESO)
        self.assertFalse(resolucion.permitido)

    def test_un_huerfano_en_instalacion_de_un_solo_negocio_si_resuelve(self):
        """
        El matiz: fallar cerrado donde NO hay nada que aislar dejaria un POS
        local sin reportes por un dato de aprovisionamiento. La regla es
        "denegar donde hay algo que aislar".
        """
        self.negocio_b.delete()
        huerfano = self._usuario('solo_uno', rol='ADMIN')

        resolucion = resolver_negocio(self._request(huerfano))

        self.assertEqual(resolucion.estado, Resolucion.TENANT)
        self.assertEqual(resolucion.negocio, self.negocio_a)

    def test_bajo_tenancy_un_huerfano_siempre_se_deniega(self):
        huerfano = self._usuario('huerfano_cloud', rol='ADMIN')

        with force_tenancy(True):
            resolucion = resolver_negocio(self._request(huerfano))

        self.assertEqual(resolucion.estado, Resolucion.SIN_ACCESO)

    def test_filtrar_devuelve_vacio_ante_un_fallo(self):
        """El corazon del hallazgo: un fallo no puede producir el queryset completo."""
        Sucursal.objects.create(
            codigo='N-1', nombre='Una', activa=True, negocio=self.negocio_a,
        )
        huerfano = self._usuario('sin_scope', rol='ADMIN')
        resolucion = resolver_negocio(self._request(huerfano))

        self.assertEqual(resolucion.filtrar(Sucursal.objects.all()).count(), 0)

    def test_negocio_actual_no_puede_usarse_para_decidir_global(self):
        """
        `negocio_actual()` sigue existiendo pero su `None` significa "no hay
        tenant", nunca "todos".
        """
        huerfano = self._usuario('ambiguo', rol='ADMIN')

        self.assertIsNone(negocio_actual(self._request(huerfano)))


class SeleccionDeNegocioTests(NegociosTestCase):
    """NEG-002 y NEG-010: un selector invalido no amplia el scope."""

    def setUp(self):
        super().setUp()
        self.operador = self._usuario('operador_saas', rol='SYSADMIN')

    def test_sin_selector_el_operador_es_global(self):
        resolucion = resolver_negocio(self._request(self.operador))

        self.assertTrue(resolucion.es_global)

    def test_un_negocio_inexistente_no_cae_a_global(self):
        """
        La reproduccion: un SYSADMIN pidio `?negocio=999999` y recibio las dos
        sucursales, no un error ni cero resultados.
        """
        resolucion = resolver_negocio(
            self._request(self.operador, negocio='999999')
        )

        self.assertEqual(resolucion.estado, Resolucion.SIN_ACCESO)

    def test_un_negocio_inactivo_tampoco(self):
        self.negocio_b.activo = False
        self.negocio_b.save(update_fields=['activo'])

        resolucion = resolver_negocio(
            self._request(self.operador, negocio=str(self.negocio_b.id))
        )

        self.assertEqual(resolucion.estado, Resolucion.SIN_ACCESO)

    def test_un_selector_no_numerico_no_revienta(self):
        """NEG-010: antes provocaba una excepcion aguas abajo."""
        resolucion = resolver_negocio(
            self._request(self.operador, negocio='no-soy-un-id')
        )

        self.assertEqual(resolucion.estado, Resolucion.SIN_ACCESO)

    def test_un_selector_valido_acota(self):
        resolucion = resolver_negocio(
            self._request(self.operador, negocio=str(self.negocio_b.id))
        )

        self.assertEqual(resolucion.estado, Resolucion.TENANT)
        self.assertEqual(resolucion.negocio, self.negocio_b)


class RevocacionDelTenantTests(NegociosTestCase):
    """NEG-003: desactivar un negocio revoca a todos sus usuarios."""

    def test_un_admin_del_negocio_desactivado_pierde_acceso(self):
        """
        La reproduccion: un ADMIN asignado a un negocio desactivado inicio
        sesion y consulto su sucursal con 200, mientras un operador granular
        equivalente quedaba correctamente rechazado.
        """
        admin = self._usuario('duena', rol='ADMIN', negocio=self.negocio_a)
        self.assertTrue(resolver_negocio(self._request(admin)).permitido)

        self.negocio_a.activo = False
        self.negocio_a.save(update_fields=['activo'])

        resolucion = resolver_negocio(self._request(admin))
        self.assertEqual(resolucion.estado, Resolucion.SIN_ACCESO)
        self.assertIn('desactivado', resolucion.motivo)

    def test_un_usuario_desactivado_tampoco_resuelve(self):
        usuario = self._usuario('inactivo', negocio=self.negocio_a)
        usuario.activo = False
        usuario.save(update_fields=['activo'])

        self.assertEqual(
            resolver_negocio(self._request(usuario)).estado, Resolucion.SIN_ACCESO,
        )

    def test_un_anonimo_no_resuelve(self):
        from django.contrib.auth.models import AnonymousUser

        peticion = self.factory.get('/')
        peticion.user = AnonymousUser()

        self.assertEqual(
            resolver_negocio(peticion).estado, Resolucion.SIN_ACCESO,
        )


class AutoridadGlobalTests(NegociosTestCase):
    """NEG-004: el rol legacy no fabrica autoridad de plataforma."""

    def test_bajo_tenancy_el_rol_legacy_no_alcanza(self):
        """
        La reproduccion: una cuenta ordinaria, no staff, no superusuario, sin
        negocio y sin identidad global pudo seleccionar el negocio B solo por
        tener el texto legacy `SYSADMIN` en una fila tenant-local y editable.
        """
        usuario = self._usuario('falso_sysadmin', rol='SYSADMIN')

        with force_tenancy(True):
            self.assertFalse(es_principal_global(usuario))

    def test_sin_tenancy_el_rol_legacy_sigue_valiendo(self):
        """
        No hay control plane con el cual contrastar: en el POS local `SYSADMIN`
        es la forma de identificar al operador de la instalacion.
        """
        usuario = self._usuario('sysadmin_local', rol='SYSADMIN')

        self.assertTrue(es_principal_global(usuario))

    def test_el_superusuario_siempre_alcanza(self):
        root = User.objects.create_superuser('root_neg', 'root_neg@test.local', 'x')

        with force_tenancy(True):
            self.assertTrue(es_principal_global(root))

    def test_un_desactivado_no_es_principal_global(self):
        usuario = self._usuario('ex_operador', rol='SYSADMIN')
        usuario.activo = False
        usuario.save(update_fields=['activo'])

        self.assertFalse(es_principal_global(usuario))


class SelfRowTests(NegociosTestCase):
    """NEG-005: el provisioning no elige el self-row en silencio."""

    def test_con_dos_filas_se_detiene(self):
        """
        La reproduccion: dos filas `Negocio` distintas pasaron `full_clean()` y
        coexistieron; `bootstrap_tenant` retitulaba la de menor PK y dejaba la
        otra sin reconciliar, partiendo el tenant en dos.
        """
        with self.assertRaises(NegocioAmbiguo):
            Negocio.self_row()

    def test_con_una_fila_la_devuelve(self):
        self.negocio_b.delete()

        self.assertEqual(Negocio.self_row(), self.negocio_a)

    def test_sin_filas_devuelve_none(self):
        self.negocio_a.delete()
        self.negocio_b.delete()

        self.assertIsNone(Negocio.self_row())

    def test_el_provisioning_ya_no_elige_por_pk(self):
        import inspect
        import re

        from apps.tenancy.management.commands import (
            bootstrap_tenant,
            normalizar_import_tenant,
        )

        # Frontera de palabra a proposito: `ConfiguracionNegocio.objects...`
        # CONTIENE la subcadena `Negocio.objects...` y daba un falso positivo.
        patron = re.compile(r"(?<![A-Za-z])Negocio\.objects\.order_by\('id'\)")

        for modulo in (bootstrap_tenant, normalizar_import_tenant):
            with self.subTest(modulo=modulo.__name__):
                fuente = inspect.getsource(modulo)
                self.assertIsNone(patron.search(fuente))
                self.assertIn('Negocio.self_row()', fuente)
