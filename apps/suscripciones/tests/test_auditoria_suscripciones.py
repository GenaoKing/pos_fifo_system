"""
apps/suscripciones/tests/test_auditoria_suscripciones.py

Regresion de los hallazgos de
`docs/exploracion/AUDITORIA_CODIGO_APPS_SUSCRIPCIONES.md`.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from rest_framework.test import APIClient

from apps.negocios.models import Negocio
from apps.suscripciones import registry, seed
from apps.suscripciones.engine import (
    CON_PLAN,
    CUSTOM,
    SIN_APROVISIONAR,
    SUSPENDIDA,
    _cache_key,
    estado_suscripcion,
    modulo_activo,
    modulos_negocio,
)
from apps.suscripciones.models import (
    Modulo,
    NegocioModulo,
    Plan,
    SucursalModuloOverride,
    SuscripcionNegocio,
)
from apps.sucursales.models import Sucursal
from apps.tenancy.context import force_tenancy

User = get_user_model()


class SuscripcionesTestCase(TestCase):
    def setUp(self):
        cache.clear()
        seed.sembrar_modulos(Modulo)
        seed.crear_planes_default(Plan, Modulo)
        self.negocio = Negocio.objects.create(nombre='Royal', slug='royal')
        self.sucursal = Sucursal.objects.create(
            codigo='SUS-A', nombre='Tienda', activa=True, negocio=self.negocio,
        )
        self.basico = Plan.objects.get(slug='basico')
        self.empresarial = Plan.objects.get(slug='empresarial')

    def tearDown(self):
        cache.clear()

    def _usuario(self, username, rol='SYSADMIN', negocio=None):
        return User.objects.create_user(
            username=username, email=f'{username}@test.local', password='x',
            rol=rol, activo=True, negocio=negocio,
        )

    def _api(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client


class EstadosDeAprovisionamientoTests(SuscripcionesTestCase):
    """SUS-001: suspender o vaciar ya no habilita todo."""

    def test_sin_suscripcion_ni_overrides_es_sin_aprovisionar(self):
        """
        El unico caso de contingencia legitimo: nadie decidio nada todavia y
        una instalacion recien montada no puede quedar sin funciones.
        """
        self.assertEqual(estado_suscripcion(self.negocio), SIN_APROVISIONAR)
        self.assertEqual(modulos_negocio(self.negocio), set(registry.keys()))

    def test_una_suscripcion_inactiva_no_habilita_todo(self):
        """
        La reproduccion: `activa=False` sin overrides devolvia exactamente
        TODAS las keys. La operacion que parece suspender hacia lo contrario.
        """
        SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=self.empresarial, activa=False,
        )

        self.assertEqual(estado_suscripcion(self.negocio), SUSPENDIDA)
        efectivo = modulos_negocio(self.negocio)
        self.assertEqual(efectivo, registry.core_keys())
        self.assertNotIn('ecf', efectivo)

    def test_plan_null_no_habilita_todo(self):
        """La otra mitad: un PATCH `plan=null` devolvia todos los modulos."""
        SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=None, activa=True,
        )

        self.assertEqual(estado_suscripcion(self.negocio), CUSTOM)
        efectivo = modulos_negocio(self.negocio)
        self.assertNotEqual(efectivo, set(registry.keys()))
        self.assertEqual(efectivo, registry.core_keys())

    def test_borrar_el_plan_tampoco(self):
        """`SET_NULL` al borrar el plan dejaba `plan_id=NULL` y habilitaba todo."""
        SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=self.basico, activa=True,
        )
        self.basico.delete()
        cache.clear()

        self.negocio.refresh_from_db()
        efectivo = modulos_negocio(Negocio.objects.get(pk=self.negocio.pk))
        self.assertNotEqual(efectivo, set(registry.keys()))

    def test_borrar_el_ultimo_override_de_una_custom_tampoco(self):
        suscripcion = SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=None, activa=True,
        )
        override = NegocioModulo.objects.create(
            negocio=self.negocio, modulo=Modulo.objects.get(key='ecf'),
            incluido=True,
        )
        cache.clear()
        self.assertIn('ecf', modulos_negocio(self.negocio))

        override.delete()
        cache.clear()

        efectivo = modulos_negocio(Negocio.objects.get(pk=self.negocio.pk))
        self.assertNotEqual(efectivo, set(registry.keys()))
        self.assertNotIn('ecf', efectivo)

    def test_un_plan_activo_da_exactamente_su_plan(self):
        SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=self.basico, activa=True,
        )

        self.assertEqual(estado_suscripcion(self.negocio), CON_PLAN)
        efectivo = modulos_negocio(self.negocio)
        self.assertIn('impresion_termica', efectivo)
        self.assertNotIn('ecf', efectivo)

    def test_ninguna_baja_aumenta_capacidades(self):
        """El criterio de cierre de la auditoria, como invariante."""
        suscripcion = SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=self.empresarial, activa=True,
        )
        cache.clear()
        antes = modulos_negocio(self.negocio)

        suscripcion.activa = False
        suscripcion.save()
        cache.clear()
        despues = modulos_negocio(Negocio.objects.get(pk=self.negocio.pk))

        self.assertTrue(despues <= antes, f'{despues - antes} aparecieron al suspender')


class AislamientoDeCacheTests(SuscripcionesTestCase):
    """SUS-002 y SUS-003."""

    def test_la_clave_lleva_el_namespace_del_tenant(self):
        clave = _cache_key(1)

        self.assertIn('local', clave)

    def test_dos_tenants_con_el_mismo_pk_no_comparten_entrada(self):
        """
        La reproduccion: dos objetos de contextos distintos con `pk=1`
        resolvieron una sola vez; el segundo recibio el set del primero.
        """
        from apps.tenancy.context import reset_current_tenant, set_current_tenant

        claves = set()
        for key in ('royalplast', 'skperformance'):
            with force_tenancy(True):
                tokens = set_current_tenant(key, 'default')
                try:
                    claves.add(_cache_key(1))
                finally:
                    reset_current_tenant(tokens)

        self.assertEqual(len(claves), 2)

    def test_sin_tenant_activo_bajo_tenancy_falla_fuerte(self):
        from apps.tenancy.context import TenantContextError

        with force_tenancy(True):
            with self.assertRaises(TenantContextError):
                _cache_key(1)

    def test_el_ttl_local_es_corto(self):
        """
        SUS-003: con `LocMemCache` y tres workers, un modulo revocado seguia
        vivo hasta 300 s en dos de cada tres requests.
        """
        from apps.suscripciones import engine

        self.assertFalse(engine._cache_compartido())
        self.assertLessEqual(engine.CACHE_TIMEOUT_LOCAL, 60)


class ScopeDeSucursalTests(SuscripcionesTestCase):
    """SUS-005: el override local llega al gate DRF."""

    def setUp(self):
        super().setUp()
        SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=self.empresarial, activa=True,
        )
        SucursalModuloOverride.objects.create(
            sucursal=self.sucursal,
            modulo=Modulo.objects.get(key='cuentas_por_cobrar'),
            activo=False,
        )
        cache.clear()

    def test_el_motor_respeta_el_override_local(self):
        self.assertTrue(modulo_activo('cuentas_por_cobrar', negocio=self.negocio))
        self.assertFalse(
            modulo_activo(
                'cuentas_por_cobrar',
                negocio=self.negocio, sucursal=self.sucursal,
            )
        )

    def test_un_usuario_sin_negocio_con_sucursal_no_es_fail_open(self):
        """
        La reproduccion: un usuario de servicio con `negocio=NULL` y token
        ligado a una sucursal cuyo plan no incluye el modulo obtenia permiso
        igual, porque el gate solo miraba `user.negocio`.
        """
        self.assertFalse(
            modulo_activo(
                'cuentas_por_cobrar', negocio=None, sucursal=self.sucursal,
            )
        )

    def test_sin_negocio_ni_sucursal_sigue_siendo_fail_open(self):
        """Los modulos son comerciales: un tenant indeterminado no deja sin POS."""
        self.assertTrue(modulo_activo('cuentas_por_cobrar'))

    def test_el_gate_drf_resuelve_la_sucursal_del_request(self):
        from apps.api.permissions import requiere_modulo

        gate = requiere_modulo('cuentas_por_cobrar')()
        usuario = self._usuario('cajero_sus', rol='CAJERA', negocio=self.negocio)

        peticion = RequestFactory().get('/')
        peticion.user = usuario
        peticion.auth = None
        peticion.sucursal = self.sucursal

        self.assertFalse(gate.has_permission(peticion, None))

    def test_el_gate_drf_sin_sucursal_usa_el_negocio(self):
        from apps.api.permissions import requiere_modulo

        gate = requiere_modulo('cuentas_por_cobrar')()
        usuario = self._usuario('cajero_sus2', rol='CAJERA', negocio=self.negocio)

        peticion = RequestFactory().get('/')
        peticion.user = usuario
        peticion.auth = None
        peticion.sucursal = None

        self.assertTrue(gate.has_permission(peticion, None))


class GuardDeDegradacionTests(SuscripcionesTestCase):
    """SUS-004: PATCH parcial, DELETE y cambio de plan no lo evaden."""

    # El guard se simula bloqueante, que es como la auditoria reprodujo el
    # hallazgo: lo que se prueba aca no es la logica de `puede_desactivarse`
    # —tiene sus propias pruebas— sino que TODAS las rutas del operador la
    # llamen. Antes, `_validar()` solo actuaba si `validated_data` traia
    # `incluido=False`, `modulo` y `negocio` a la vez, `destroy` no estaba
    # sobrescrito, y el cambio de plan no calculaba los modulos retirados.
    RUTA_GUARD = 'apps.api.views.suscripciones.puede_desactivarse'

    def setUp(self):
        super().setUp()
        self.operador = self._usuario('op_sus')
        self.suscripcion = SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=self.empresarial, activa=True,
        )
        cache.clear()

    def _bloqueado(self):
        from unittest.mock import patch

        return patch(self.RUTA_GUARD, return_value=(False, 'hay datos en vuelo'))

    def _override_incluido(self, key='cuentas_por_cobrar'):
        override = NegocioModulo.objects.create(
            negocio=self.negocio, modulo=Modulo.objects.get(key=key),
            incluido=True,
        )
        cache.clear()
        return override

    def test_un_patch_parcial_no_esquiva_el_guard(self):
        """
        La reproduccion: un PATCH de solo `incluido` retornaba sin validar,
        porque `_validar()` exigia los tres campos a la vez.
        """
        override = self._override_incluido()

        with self._bloqueado():
            respuesta = self._api(self.operador).patch(
                f'/api/v1/suscripciones/overrides/{override.id}/',
                {'incluido': False}, format='json',
            )

        self.assertEqual(respuesta.status_code, 400)
        override.refresh_from_db()
        self.assertTrue(override.incluido)

    def test_un_delete_tampoco(self):
        """
        `destroy` no estaba sobrescrito y no llamaba el guard.

        El override tiene que ser la UNICA fuente del modulo para que borrarlo
        lo retire: sobre un plan que ya lo incluye, borrar un `incluido=True`
        redundante no degrada nada y 204 es la respuesta correcta.
        """
        self.suscripcion.plan = None
        self.suscripcion.save()
        override = self._override_incluido()
        cache.clear()
        self.assertIn('cuentas_por_cobrar', modulos_negocio(self.negocio))

        with self._bloqueado():
            respuesta = self._api(self.operador).delete(
                f'/api/v1/suscripciones/overrides/{override.id}/'
            )

        self.assertEqual(respuesta.status_code, 400)
        self.assertTrue(NegocioModulo.objects.filter(pk=override.pk).exists())

    def test_bajar_de_plan_tampoco(self):
        """Cambiar Empresarial a Basico no calculaba los modulos retirados."""
        with self._bloqueado():
            respuesta = self._api(self.operador).patch(
                f'/api/v1/suscripciones/negocios/{self.suscripcion.id}/',
                {'plan': self.basico.slug}, format='json',
            )

        self.assertEqual(respuesta.status_code, 400)
        self.suscripcion.refresh_from_db()
        self.assertEqual(self.suscripcion.plan.slug, self.empresarial.slug)

    def test_suspender_con_datos_en_vuelo_tampoco(self):
        """
        Suspender retira TODO menos core: si algo no puede irse, la suspension
        tiene que fallar igual que una exclusion puntual.
        """
        with self._bloqueado():
            respuesta = self._api(self.operador).patch(
                f'/api/v1/suscripciones/negocios/{self.suscripcion.id}/',
                {'activa': False}, format='json',
            )

        self.assertEqual(respuesta.status_code, 400)
        self.suscripcion.refresh_from_db()
        self.assertTrue(self.suscripcion.activa)

    def test_sin_datos_bloqueantes_la_baja_procede(self):
        respuesta = self._api(self.operador).patch(
            f'/api/v1/suscripciones/negocios/{self.suscripcion.id}/',
            {'plan': self.basico.slug}, format='json',
        )

        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.suscripcion.refresh_from_db()
        self.assertEqual(self.suscripcion.plan.slug, self.basico.slug)

    def test_una_transicion_rechazada_no_deja_escritura_parcial(self):
        """
        El guard corre DENTRO de la transaccion: si rechaza, la escritura que ya
        se hizo tiene que revertirse.
        """
        override = self._override_incluido()

        with self._bloqueado():
            self._api(self.operador).patch(
                f'/api/v1/suscripciones/overrides/{override.id}/',
                {'incluido': False}, format='json',
            )

        cache.clear()
        self.assertIn('cuentas_por_cobrar', modulos_negocio(self.negocio))
