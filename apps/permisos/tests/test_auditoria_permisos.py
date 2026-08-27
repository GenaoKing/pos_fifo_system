"""
apps/permisos/tests/test_auditoria_permisos.py

Regresion de los hallazgos de `docs/exploracion/AUDITORIA_CODIGO_APPS_PERMISOS.md`.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase, override_settings

from apps.negocios.utils import negocio_actual
from apps.permisos import engine, testing
from apps.permisos.catalogo import sembrar_catalogo
from apps.permisos.models import AsignacionRol, Permiso
from apps.sucursales.models import Sucursal

User = get_user_model()

CACHE_COMPARTIDO = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'compartido-de-mentira',
    }
}


class PermisosTestCase(TestCase):
    def setUp(self):
        cache.clear()
        engine.limpiar_memo()
        sembrar_catalogo(Permiso)
        self.negocio_a = testing.crear_negocio('Negocio A')
        self.negocio_b = testing.crear_negocio('Negocio B')

    def tearDown(self):
        cache.clear()
        engine.limpiar_memo()

    def _usuario(self, username, rol='CAJERA', negocio=None):
        user = User.objects.create_user(
            username=username, email=f'{username}@test.local',
            password='x', rol=rol, activo=True,
        )
        if negocio is not None:
            user.negocio = negocio
            user.save(update_fields=['negocio'])
        return user

    def _sucursal(self, codigo, negocio):
        return Sucursal.objects.create(
            codigo=codigo, nombre=f'Sucursal {codigo}', activa=True, negocio=negocio,
        )


class ScopeDeSucursalTests(PermisosTestCase):
    """PER-003: omitir la sucursal ya no une todas las sucursales."""

    def setUp(self):
        super().setUp()
        self.suc_a = self._sucursal('SC-A', self.negocio_a)
        self.suc_b = self._sucursal('SC-B', self.negocio_a)
        self.rol = testing.crear_rol(self.negocio_a, 'Supervisor', ['ventas.anular'])
        self.usuario = self._usuario('acotado', negocio=self.negocio_a)
        testing.asignar(self.usuario, self.rol, sucursal=self.suc_a)

    def test_sin_scope_un_permiso_de_a_ya_no_aplica(self):
        """
        La reproduccion de la auditoria: el permiso se concedio SOLO en A y
        `tiene_permiso('ventas.anular')` sin scope devolvia True.
        """
        self.assertFalse(self.usuario.tiene_permiso('ventas.anular'))

    def test_aplica_en_su_sucursal_y_no_en_la_otra(self):
        self.assertTrue(
            self.usuario.tiene_permiso('ventas.anular', sucursal=self.suc_a)
        )
        self.assertFalse(
            self.usuario.tiene_permiso('ventas.anular', sucursal=self.suc_b)
        )

    def test_una_asignacion_global_si_aplica_sin_scope(self):
        otro = self._usuario('global', negocio=self.negocio_a)
        testing.asignar(otro, self.rol, sucursal=None)

        self.assertTrue(otro.tiene_permiso('ventas.anular'))
        self.assertTrue(otro.tiene_permiso('ventas.anular', sucursal=self.suc_b))

    def test_todas_es_el_centinela_explicito_de_la_union(self):
        """
        La union sigue existiendo, pero hay que pedirla por su nombre. La usa
        el login del portal para preguntar "¿puede algo en alguna parte?".
        """
        self.assertIn(
            'ventas.anular',
            engine.permisos_de_usuario(self.usuario, sucursal=engine.TODAS),
        )
        self.assertEqual(engine.permisos_de_usuario(self.usuario), set())

    def test_el_decorador_resuelve_la_sucursal_del_request(self):
        from apps.permisos.decorators import requiere_permiso_json

        @requiere_permiso_json('ventas.anular')
        def vista(request):
            from django.http import JsonResponse
            return JsonResponse({'ok': True})

        factory = RequestFactory()

        request = factory.get('/')
        request.user = self.usuario
        request.sucursal = self.suc_a
        self.assertEqual(vista(request).status_code, 200)

        request = factory.get('/')
        request.user = self.usuario
        request.sucursal = self.suc_b
        self.assertEqual(vista(request).status_code, 403)

    def test_el_filtro_de_plantilla_usa_la_sucursal_de_la_instalacion(self):
        from apps.permisos.templatetags.permisos import puede

        with self.settings(SUCURSAL_CODIGO='SC-A'):
            cache.clear()
            engine.limpiar_memo()
            self.assertTrue(puede(self.usuario, 'ventas.anular'))

        with self.settings(SUCURSAL_CODIGO='SC-B'):
            cache.clear()
            engine.limpiar_memo()
            self.assertFalse(puede(self.usuario, 'ventas.anular'))


class AislamientoDeCacheTests(PermisosTestCase):
    """PER-001 y PER-002."""

    def test_la_clave_de_cache_lleva_el_namespace_del_tenant(self):
        clave = engine._cache_key('royalplast', 777, None)
        otra = engine._cache_key('skperformance', 777, None)

        self.assertNotEqual(clave, otra)
        self.assertIn('royalplast', clave)

    def test_dos_tenants_con_el_mismo_pk_no_comparten_entrada(self):
        """
        Los PK se reinician por base tenant: el usuario 777 de un negocio y el
        777 de otro son personas distintas. Antes la clave era solo
        `usuario_id:sucursal_id` y compartian el set dentro del worker.
        """
        vistas = set()
        for tenant in ('royalplast', 'skperformance'):
            vistas.add(engine._cache_key(tenant, 777, None))
        self.assertEqual(len(vistas), 2)

    def test_con_backend_local_no_se_cachea_entre_requests(self):
        """
        PER-002. Con `LocMemCache` y tres workers de Gunicorn, cachear entre
        requests deja a dos procesos autorizando con datos revocados. El motor
        detecta el backend local y solo memoiza dentro del request.
        """
        self.assertFalse(engine._cache_compartido())

        rol = testing.crear_rol(self.negocio_a, 'Cajero', ['ventas.crear'])
        usuario = self._usuario('sin_cache_cruzado', negocio=self.negocio_a)
        testing.asignar(usuario, rol)

        self.assertTrue(usuario.tiene_permiso('ventas.crear'))

        # Simula el siguiente request: el memo se descarta, el cache compartido
        # no existe, asi que se vuelve a resolver contra la BD.
        engine.limpiar_memo()
        clave = engine._cache_key(engine._namespace(), usuario.pk, None)
        self.assertIsNone(cache.get(clave))

    @override_settings(CACHES=CACHE_COMPARTIDO)
    def test_un_backend_compartido_si_cachea_entre_requests(self):
        # `_cache_compartido` mira el BACKEND; para ejercitar la rama se fuerza
        # la deteccion, que es lo que decide el comportamiento.
        original = engine._cache_compartido
        engine._cache_compartido = lambda: True
        try:
            rol = testing.crear_rol(self.negocio_a, 'Cajero', ['ventas.crear'])
            usuario = self._usuario('con_cache', negocio=self.negocio_a)
            testing.asignar(usuario, rol)

            self.assertTrue(usuario.tiene_permiso('ventas.crear'))
            clave = engine._cache_key(engine._namespace(), usuario.pk, None)
            self.assertIsNotNone(cache.get(clave))
        finally:
            engine._cache_compartido = original

    def test_el_memo_se_limpia_por_request(self):
        from apps.permisos.middleware import PermisosRequestCacheMiddleware

        rol = testing.crear_rol(self.negocio_a, 'Cajero', ['ventas.crear'])
        usuario = self._usuario('memo', negocio=self.negocio_a)
        testing.asignar(usuario, rol)
        usuario.tiene_permiso('ventas.crear')

        self.assertIsNotNone(engine._memo.get())

        middleware = PermisosRequestCacheMiddleware(lambda request: 'respuesta')
        middleware(RequestFactory().get('/'))

        self.assertIsNone(engine._memo.get())


class NegocioCruzadoTests(PermisosTestCase):
    """PER-004: usuario, rol y sucursal tienen que ser del mismo negocio."""

    def test_una_asignacion_cruzada_no_pasa_full_clean(self):
        rol_a = testing.crear_rol(self.negocio_a, 'Admin A', ['permisos.administrar'])
        usuario_b = self._usuario('de_b', negocio=self.negocio_b)

        asignacion = AsignacionRol(usuario=usuario_b, rol=rol_a, activo=True)

        with self.assertRaises(ValidationError):
            asignacion.full_clean()

    def test_el_motor_ignora_una_fila_cruzada_que_ya_exista(self):
        """
        La ultima linea de defensa: aunque una importacion, el admin o un bug
        de API dejen la fila, el resolver no la convierte en privilegio.
        """
        rol_a = testing.crear_rol(self.negocio_a, 'Admin A', ['permisos.administrar'])
        usuario_b = self._usuario('colado', negocio=self.negocio_b)
        AsignacionRol.objects.create(usuario=usuario_b, rol=rol_a, activo=True)

        self.assertFalse(usuario_b.tiene_permiso('permisos.administrar'))

    def test_una_sucursal_de_otro_negocio_no_pasa_full_clean(self):
        rol_a = testing.crear_rol(self.negocio_a, 'Cajero', ['ventas.crear'])
        usuario_a = self._usuario('propio', negocio=self.negocio_a)
        sucursal_b = self._sucursal('SC-B1', self.negocio_b)

        asignacion = AsignacionRol(
            usuario=usuario_a, rol=rol_a, sucursal=sucursal_b, activo=True,
        )
        with self.assertRaises(ValidationError):
            asignacion.full_clean()

    def test_un_usuario_sin_negocio_no_recibe_permisos_de_tenant(self):
        rol_a = testing.crear_rol(self.negocio_a, 'Admin A', ['permisos.administrar'])
        huerfano = self._usuario('sin_negocio')
        huerfano.negocio = None
        huerfano.save(update_fields=['negocio'])
        AsignacionRol.objects.create(usuario=huerfano, rol=rol_a, activo=True)

        self.assertFalse(huerfano.tiene_permiso('permisos.administrar'))


class EleccionDeNegocioTests(PermisosTestCase):
    """PER-005: `?negocio=` exige identidad global."""

    def _request(self, user, negocio_id):
        request = RequestFactory().get('/', {'negocio': str(negocio_id)})
        request.user = user
        return request

    def test_un_usuario_sin_negocio_ya_no_elige_cualquiera(self):
        """
        La escalada reproducida: un ADMIN de A le asigna un rol a un usuario
        con `negocio=NULL`; ese usuario pide `?negocio=<B>` y administraba B.
        """
        huerfano = self._usuario('escalador')
        huerfano.negocio = None
        huerfano.save(update_fields=['negocio'])

        self.assertIsNone(negocio_actual(self._request(huerfano, self.negocio_b.id)))

    def test_un_sysadmin_si_puede_elegir(self):
        operador = self._usuario('operador', rol='SYSADMIN')
        operador.negocio = None
        operador.save(update_fields=['negocio'])

        self.assertEqual(
            negocio_actual(self._request(operador, self.negocio_b.id)),
            self.negocio_b,
        )

    def test_el_query_param_no_saca_a_nadie_de_su_negocio(self):
        propio = self._usuario('con_negocio', negocio=self.negocio_a)

        self.assertEqual(
            negocio_actual(self._request(propio, self.negocio_b.id)),
            self.negocio_a,
        )

    def test_un_usuario_desactivado_no_resuelve_negocio(self):
        propio = self._usuario('desactivado', negocio=self.negocio_a)
        propio.activo = False
        propio.save(update_fields=['activo'])

        self.assertIsNone(negocio_actual(self._request(propio, self.negocio_a.id)))


class UnicidadDeAsignacionTests(PermisosTestCase):
    """PER-008: la unicidad global ahora existe de verdad."""

    def setUp(self):
        super().setUp()
        self.rol = testing.crear_rol(self.negocio_a, 'Cajero', ['ventas.anular'])
        self.usuario = self._usuario('unico', negocio=self.negocio_a)

    def test_no_se_pueden_crear_dos_asignaciones_globales_iguales(self):
        AsignacionRol.objects.create(usuario=self.usuario, rol=self.rol, activo=True)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AsignacionRol.objects.create(
                    usuario=self.usuario, rol=self.rol, activo=True,
                )

    def test_revocar_la_unica_fila_revoca_de_verdad(self):
        asignacion = AsignacionRol.objects.create(
            usuario=self.usuario, rol=self.rol, activo=True,
        )
        self.assertTrue(self.usuario.tiene_permiso('ventas.anular'))

        asignacion.activo = False
        asignacion.save()

        self.assertFalse(self.usuario.tiene_permiso('ventas.anular'))

    def test_la_misma_terna_con_sucursal_distinta_si_convive(self):
        suc1 = self._sucursal('U-1', self.negocio_a)
        suc2 = self._sucursal('U-2', self.negocio_a)

        AsignacionRol.objects.create(usuario=self.usuario, rol=self.rol, sucursal=suc1)
        AsignacionRol.objects.create(usuario=self.usuario, rol=self.rol, sucursal=suc2)

        self.assertEqual(AsignacionRol.objects.count(), 2)


class RevocacionInmediataTests(PermisosTestCase):
    """PER-010: desactivar o degradar surte efecto en el proximo request."""

    def test_un_usuario_desactivado_pierde_los_permisos(self):
        rol = testing.crear_rol(self.negocio_a, 'Cajero', ['ventas.crear'])
        usuario = self._usuario('a_desactivar', negocio=self.negocio_a)
        testing.asignar(usuario, rol)
        self.assertTrue(usuario.tiene_permiso('ventas.crear'))

        usuario.activo = False
        usuario.save(update_fields=['activo'])

        self.assertFalse(usuario.tiene_permiso('ventas.crear'))

    def test_is_active_sigue_a_activo(self):
        """
        `AbstractBaseUser` define `is_active = True` y el modelo no lo
        redefinia: Django veia activo a TODO usuario, asi que una sesion ya
        emitida sobrevivia a la desactivacion.
        """
        usuario = self._usuario('sesion_viva', negocio=self.negocio_a)
        self.assertTrue(usuario.is_active)

        usuario.activo = False
        usuario.save(update_fields=['activo'])

        self.assertFalse(usuario.is_active)

    def test_degradar_el_rol_legacy_revoca_el_acceso_total(self):
        """
        La reproduccion de la auditoria: se precarga el catalogo de un ADMIN, se
        lo baja a CAJERA y `permisos.administrar` seguia devolviendo True desde
        el cache porque ninguna senal cambiaba la version.
        """
        usuario = self._usuario('degradado', rol='ADMIN', negocio=self.negocio_a)
        self.assertTrue(usuario.tiene_permiso('permisos.administrar'))

        usuario.rol = 'CAJERA'
        usuario.save(update_fields=['rol'])

        self.assertFalse(usuario.tiene_permiso('permisos.administrar'))

    def test_desactivar_el_negocio_revoca_a_sus_usuarios(self):
        rol = testing.crear_rol(self.negocio_a, 'Cajero', ['ventas.crear'])
        usuario = self._usuario('negocio_off', negocio=self.negocio_a)
        testing.asignar(usuario, rol)
        self.assertTrue(usuario.tiene_permiso('ventas.crear'))

        self.negocio_a.activo = False
        self.negocio_a.save(update_fields=['activo'])

        self.assertFalse(usuario.tiene_permiso('ventas.crear'))

    def test_una_sucursal_inactiva_no_habilita_su_asignacion(self):
        sucursal = self._sucursal('OFF-1', self.negocio_a)
        rol = testing.crear_rol(self.negocio_a, 'Cajero', ['ventas.crear'])
        usuario = self._usuario('suc_off', negocio=self.negocio_a)
        testing.asignar(usuario, rol, sucursal=sucursal)
        self.assertTrue(usuario.tiene_permiso('ventas.crear', sucursal=sucursal))

        sucursal.activa = False
        sucursal.save(update_fields=['activa'])

        self.assertFalse(usuario.tiene_permiso('ventas.crear', sucursal=sucursal))


class LimitesDelAccesoTotalTests(PermisosTestCase):
    """PER-009."""

    def test_un_codigo_con_typo_deniega_a_todos(self):
        for rol in ('ADMIN', 'SYSADMIN'):
            with self.subTest(rol=rol):
                usuario = self._usuario(f'typo_{rol}', rol=rol, negocio=self.negocio_a)
                self.assertFalse(usuario.tiene_permiso('ventas.anulr'))

    def test_el_admin_del_tenant_no_alcanza_al_operador_saas(self):
        admin = self._usuario('duena', rol='ADMIN', negocio=self.negocio_a)
        self.assertFalse(admin.tiene_permiso('suscripciones.administrar'))

    def test_el_operador_si(self):
        operador = self._usuario('op_saas', rol='SYSADMIN')
        self.assertTrue(operador.tiene_permiso('suscripciones.administrar'))

    def test_el_catalogo_declarativo_cubre_todos_los_gates_reales(self):
        """
        Que un codigo desconocido deniegue solo es seguro si el catalogo cubre
        el enforcement real (PER-013). Este test lo mantiene cierto: si alguien
        agrega un gate con un codigo que no declaro, falla aca y no en
        produccion.
        """
        import pathlib
        import re

        from django.conf import settings

        from apps.permisos.catalogo import codigos_catalogo

        patrones = [
            re.compile(r"tiene_permiso\(\s*['\"]([a-z_]+\.[a-z_.]+)['\"]"),
            re.compile(
                r"requiere_permiso_(?:local|json)\(\s*['\"]([a-z_]+\.[a-z_.]+)['\"]"
            ),
            re.compile(r"requiere_permiso\(\s*['\"]([a-z_]+\.[a-z_.]+)['\"]"),
            re.compile(r"\|puede:'([a-z_]+\.[a-z_.]+)'"),
        ]
        catalogo = codigos_catalogo()
        raiz = pathlib.Path(settings.BASE_DIR)
        huerfanos = {}

        for base in ('apps', 'templates'):
            for archivo in (raiz / base).rglob('*'):
                if archivo.suffix not in ('.py', '.html'):
                    continue
                if 'migrations' in archivo.parts or 'tests' in archivo.parts:
                    continue
                texto = archivo.read_text(encoding='utf-8', errors='ignore')
                for patron in patrones:
                    for encontrado in patron.finditer(texto):
                        codigo = encontrado.group(1)
                        if codigo not in catalogo:
                            huerfanos.setdefault(codigo, set()).add(archivo.name)

        self.assertEqual(
            huerfanos, {},
            f'Gates que piden un permiso fuera del catalogo: {huerfanos}',
        )
