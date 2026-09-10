"""
apps/suscripciones/tests/test_auditoria_suscripciones.py

Regresion de los hallazgos de
`docs/exploracion/AUDITORIA_CODIGO_APPS_SUSCRIPCIONES.md`.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from rest_framework.test import APIClient

from apps.configuracion.models import ConfiguracionNegocio
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
    modulos_activos,
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


class HooksDeDatosBloqueantesTests(SuscripcionesTestCase):
    """SUS-010: un error al comprobar datos en vuelo NO autoriza la baja."""

    def setUp(self):
        super().setUp()
        SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=self.empresarial, activa=True,
        )
        cache.clear()

    def test_una_excepcion_del_hook_bloquea_la_baja(self):
        """
        La reproduccion: un hook que lanzo `ZeroDivisionError` producia
        `(True, '')` y autorizaba la baja. Un fallo de infra (tabla
        indisponible, error de esquema) se leia como "no hay datos pendientes".
        """
        from unittest.mock import patch

        from apps.suscripciones import engine

        def revienta(_negocio):
            raise ZeroDivisionError('tabla indisponible')

        with patch.dict(engine._HOOKS_DATOS, {'cuentas_por_cobrar': revienta}):
            with self.assertLogs('suscripciones', level='ERROR'):
                ok, motivo = engine.puede_desactivarse(
                    self.negocio, 'cuentas_por_cobrar',
                )

        self.assertFalse(ok)
        self.assertIn('no se pudo verificar', motivo)

    def test_sin_datos_ni_error_la_baja_procede(self):
        """Sin CxC abiertas y sin fallo, el modulo si se puede apagar."""
        from apps.suscripciones import engine

        ok, _ = engine.puede_desactivarse(self.negocio, 'cuentas_por_cobrar')
        self.assertTrue(ok)


class KeyDesconocidaTests(SuscripcionesTestCase):
    """SUS-018: una key fuera del catalogo deniega, aun en el camino fail-open."""

    def test_key_desconocida_sin_negocio_deniega_y_avisa(self):
        """
        La reproduccion: `modulo_con_typo` no existia en el registro y aun asi
        devolvia True sin negocio, sin ningun log que revelara el error.
        """
        with self.assertLogs('suscripciones', level='WARNING'):
            self.assertFalse(modulo_activo('modulo_con_typo', negocio=None))

    def test_key_real_sin_negocio_sigue_fail_open(self):
        """El fail-open comercial se conserva para keys reales del catalogo."""
        self.assertTrue(modulo_activo('ecf', negocio=None))


class DriftDeCatalogoTests(SuscripcionesTestCase):
    """SUS-012: el registro en codigo y el espejo DB no pueden divergir callados."""

    def test_registro_real_es_valido(self):
        from apps.suscripciones.checks import registro_de_modulos_es_valido

        self.assertEqual(registro_de_modulos_es_valido(None), [])

    def test_espejo_sembrado_no_reporta_nada(self):
        from apps.suscripciones.checks import espejo_db_coincide_con_registro

        self.assertEqual(espejo_db_coincide_con_registro(None), [])

    def test_modulo_fantasma_en_db_es_error(self):
        """
        La reproduccion: un modulo agregado solo en la DB se asignaba a un plan
        y el resolutor lo descartaba en silencio.
        """
        from apps.suscripciones.checks import espejo_db_coincide_con_registro

        Modulo.objects.create(key='fantasma', nombre='Fantasma')

        ids = {p.id for p in espejo_db_coincide_con_registro(None)}
        self.assertIn('suscripciones.E004', ids)

    def test_modulo_faltante_en_db_es_warning(self):
        from apps.suscripciones.checks import espejo_db_coincide_con_registro

        Modulo.objects.filter(key='ecf').delete()

        ids = {p.id for p in espejo_db_coincide_con_registro(None)}
        self.assertIn('suscripciones.W001', ids)

    def test_core_divergente_es_warning(self):
        from apps.suscripciones.checks import espejo_db_coincide_con_registro

        # 'ecf' es vendible en el registro; marcarlo core en la DB es drift.
        Modulo.objects.filter(key='ecf').update(core=True)

        ids = {p.id for p in espejo_db_coincide_con_registro(None)}
        self.assertIn('suscripciones.W002', ids)


class BootstrapTestCase(SuscripcionesTestCase):
    """Base para las pruebas de onboarding: llama al bootstrap con modelos reales."""

    def _bootstrap(self):
        return seed.bootstrap(
            ModuloModel=Modulo,
            PlanModel=Plan,
            NegocioModel=Negocio,
            NegocioModuloModel=NegocioModulo,
            SuscripcionModel=SuscripcionNegocio,
            ConfiguracionModel=ConfiguracionNegocio,
        )

    def _config(self, sucursal, **flags):
        return ConfiguracionNegocio.objects.create(
            sucursal=sucursal, nombre_negocio=getattr(sucursal, 'nombre', 'X'),
            **flags,
        )


class BootstrapPreservaPorSucursalTests(BootstrapTestCase):
    """SUS-008: la union del bootstrap no enciende un modulo en una sucursal que
    lo tenia apagado."""

    def test_flags_divergentes_se_conservan_bit_por_bit(self):
        """
        La reproduccion: sucursal A con e-CF=True y B con e-CF=False producia
        e-CF activo tambien para B, sin ningun override de compensacion.
        """
        suc_a = self.sucursal  # SUS-A (setUp)
        suc_b = Sucursal.objects.create(
            codigo='SUS-B', nombre='Tienda B', activa=True, negocio=self.negocio,
        )
        self._config(suc_a, modulo_ecf=True)
        self._config(suc_b, modulo_ecf=False)

        self._bootstrap()
        cache.clear()

        # A nivel negocio, e-CF esta en el set (union de A y B).
        self.assertIn('ecf', modulos_negocio(self.negocio))
        # Pero se conserva bit por bit: A encendido, B apagado.
        self.assertIn('ecf', modulos_activos(self.negocio, sucursal=suc_a))
        self.assertNotIn('ecf', modulos_activos(self.negocio, sucursal=suc_b))

    def test_todas_apagadas_no_crea_override_ni_enciende(self):
        """Si ninguna sucursal tenia el flag, no hay nada que compensar."""
        suc_a = self.sucursal
        self._config(suc_a, modulo_ecf=False)

        resumen = self._bootstrap()
        cache.clear()

        self.assertNotIn('ecf', modulos_negocio(self.negocio))
        self.assertEqual(resumen['overrides_sucursal'], 0)


class BootstrapLegacySinSucursalTests(BootstrapTestCase):
    """SUS-009: la configuracion legacy `sucursal=NULL` no se ignora en silencio."""

    def test_una_config_legacy_con_un_solo_negocio_se_adopta(self):
        self._config_legacy(modulo_ecf=True)

        resumen = self._bootstrap()
        cache.clear()

        self.assertEqual(resumen['legacy_adoptadas'], 1)
        self.assertIn('ecf', modulos_negocio(self.negocio))

    def test_config_legacy_con_varios_negocios_aborta_sin_escribir(self):
        """
        La reproduccion: la derivacion filtraba `sucursal__negocio` y la fila
        legacy se perdia. Ahora, si no es atribuible sin ambiguedad, aborta.
        """
        Negocio.objects.create(nombre='Otro', slug='otro')  # ya son 2 negocios
        self._config_legacy(modulo_ecf=True)
        antes = SuscripcionNegocio.objects.count()

        with self.assertRaises(seed.BootstrapAmbiguo):
            self._bootstrap()

        self.assertEqual(SuscripcionNegocio.objects.count(), antes)

    def _config_legacy(self, **flags):
        return ConfiguracionNegocio.objects.create(
            sucursal=None, nombre_negocio='Legacy', **flags,
        )


class BootstrapDryRunTests(BootstrapTestCase):
    """SUS-016: `--dry-run` reporta sin escribir, y el bootstrap es atomico."""

    def test_dry_run_no_escribe(self):
        from io import StringIO

        from django.core.management import call_command

        self._config(self.sucursal, modulo_ecf=True)
        antes = SuscripcionNegocio.objects.count()

        salida = StringIO()
        call_command('bootstrap_suscripciones', '--dry-run', stdout=salida)

        self.assertEqual(SuscripcionNegocio.objects.count(), antes)
        self.assertIn('DRY-RUN', salida.getvalue())


class SemanticaDePlanYOverrideTests(SuscripcionesTestCase):
    """SUS-013: `Plan.activo` bloquea nuevas altas (no suspende); core no se
    excluye ni se apaga por sucursal."""

    def setUp(self):
        super().setUp()
        self.operador = self._usuario('op_sus13')
        cache.clear()

    def test_asignar_un_plan_inactivo_a_una_nueva_alta_se_rechaza(self):
        """La reproduccion: el serializer aceptaba cualquier plan, incluido inactivo."""
        self.basico.activo = False
        self.basico.save()
        suscripcion = SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=None, activa=True,
        )
        cache.clear()

        respuesta = self._api(self.operador).patch(
            f'/api/v1/suscripciones/negocios/{suscripcion.id}/',
            {'plan': self.basico.slug}, format='json',
        )

        self.assertEqual(respuesta.status_code, 400)

    def test_reguardar_a_un_suscriptor_ya_en_el_plan_inactivo_no_se_bloquea(self):
        """`activo=False` no suspende clientes existentes: mantenerlos es valido."""
        suscripcion = SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=self.basico, activa=True,
        )
        self.basico.activo = False
        self.basico.save()
        cache.clear()

        respuesta = self._api(self.operador).patch(
            f'/api/v1/suscripciones/negocios/{suscripcion.id}/',
            {'plan': self.basico.slug}, format='json',
        )

        self.assertEqual(respuesta.status_code, 200, respuesta.content)

    def test_excluir_un_modulo_core_via_override_se_rechaza(self):
        SuscripcionNegocio.objects.create(
            negocio=self.negocio, plan=self.empresarial, activa=True,
        )
        cache.clear()

        respuesta = self._api(self.operador).post(
            '/api/v1/suscripciones/overrides/',
            {'negocio': self.negocio.id, 'modulo': 'ventas', 'incluido': False},
            format='json',
        )

        self.assertEqual(respuesta.status_code, 400)

    def test_override_de_sucursal_activo_true_se_rechaza(self):
        from django.core.exceptions import ValidationError

        override = SucursalModuloOverride(
            sucursal=self.sucursal, modulo=Modulo.objects.get(key='ecf'),
            activo=True,
        )
        with self.assertRaises(ValidationError) as ctx:
            override.full_clean()
        self.assertIn('activo', ctx.exception.message_dict)

    def test_apagar_un_core_por_sucursal_se_rechaza(self):
        from django.core.exceptions import ValidationError

        override = SucursalModuloOverride(
            sucursal=self.sucursal, modulo=Modulo.objects.get(key='ventas'),
            activo=False,
        )
        with self.assertRaises(ValidationError) as ctx:
            override.full_clean()
        self.assertIn('modulo', ctx.exception.message_dict)
