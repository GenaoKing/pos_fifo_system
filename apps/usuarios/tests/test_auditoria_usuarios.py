"""
apps/usuarios/tests/test_auditoria_usuarios.py

Regresion de los hallazgos de `docs/exploracion/AUDITORIA_CODIGO_APPS_USUARIOS.md`.

La app no tenia cobertura propia (USR-018); este modulo es el arranque.
"""
from unittest.mock import patch

from django.contrib.auth import authenticate, get_user_model
from django.core.cache import cache
from django.db.models import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.negocios.models import Negocio
from apps.permisos.models import Rol
from apps.sucursales.models import Sucursal
from apps.auditoria.models import Auditoria
from apps.usuarios.services import provisionar_usuario
from apps.usuarios.throttling import limite_login
from apps.usuarios.middleware import SESSION_INICIO_ABSOLUTO

User = get_user_model()


class UsuariosTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.negocio = Negocio.objects.create(nombre='Negocio USR', slug='negocio-usr')

    def tearDown(self):
        cache.clear()

    def _usuario(self, username='operador', rol='CAJERA', **extra):
        return User.objects.create_user(
            username=username, email=f'{username}@test.local',
            password='Prueba123', rol=rol, negocio=self.negocio, **extra,
        )


class DesactivacionRevocaTests(UsuariosTestCase):
    """USR-001: `activo` y `is_active` son lo mismo."""

    def test_is_active_sigue_a_activo(self):
        usuario = self._usuario()
        self.assertTrue(usuario.is_active)

        usuario.activo = False
        usuario.save(update_fields=['activo'])

        self.assertFalse(usuario.is_active)

    def test_authenticate_rechaza_a_un_desactivado(self):
        """
        La reproduccion de la auditoria: `Usuario(activo=False)` devolvia
        `is_active == True` y `authenticate(...)` devolvia ese usuario.
        """
        usuario = self._usuario('a_desactivar')
        self.assertIsNotNone(
            authenticate(username='a_desactivar', password='Prueba123')
        )

        usuario.activo = False
        usuario.save(update_fields=['activo'])

        self.assertIsNone(
            authenticate(username='a_desactivar', password='Prueba123')
        )

    def test_una_sesion_ya_abierta_deja_de_servir(self):
        """
        El caso mas grave: la sesion se creo antes de desactivar y seguia
        entrando a vistas con `login_required`, conservando su session key.
        """
        usuario = self._usuario('con_sesion', rol='ADMIN')
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(reverse('pos:punto_venta')).status_code, 200)

        usuario.activo = False
        usuario.save(update_fields=['activo'])

        respuesta = self.client.get(reverse('pos:punto_venta'))
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn('login', respuesta['Location'])

    def test_un_staff_desactivado_no_abre_el_admin(self):
        usuario = self._usuario(
            'staff_off', rol='SYSADMIN', is_staff=True, is_superuser=True,
        )
        self.client.force_login(usuario)
        self.assertEqual(self.client.get('/admin/').status_code, 200)

        usuario.activo = False
        usuario.save(update_fields=['activo'])

        self.assertNotEqual(self.client.get('/admin/').status_code, 200)

    def test_el_override_de_caja_ya_no_confia_en_un_desactivado(self):
        """
        `apps/caja/views.py` comprobaba `user.is_active`, no `user.activo`. Con
        los dos unificados, la comprobacion que ya existia pasa a ser correcta.
        """
        usuario = self._usuario('autorizador', rol='ADMIN')
        usuario.activo = False
        usuario.save(update_fields=['activo'])

        self.assertFalse(usuario.is_active)
        self.assertFalse(usuario.tiene_permiso('caja.administrar'))


class LogoutSeguroTests(UsuariosTestCase):
    """USR-004 y USR-009."""

    def setUp(self):
        super().setUp()
        self.usuario = self._usuario('sale', rol='ADMIN')
        self.client.force_login(self.usuario)

    def test_logout_por_get_no_cierra_la_sesion(self):
        """
        Con GET, un enlace o una imagen en otro sitio cerraba la sesion del
        operador: no hay token CSRF que verificar en un GET.
        """
        respuesta = self.client.get(reverse('usuarios:logout'))

        self.assertEqual(respuesta.status_code, 405)
        self.assertEqual(
            self.client.get(reverse('pos:punto_venta')).status_code, 200,
        )

    def test_logout_por_post_si_cierra(self):
        self.client.post(reverse('usuarios:logout'))

        respuesta = self.client.get(reverse('pos:punto_venta'))
        self.assertEqual(respuesta.status_code, 302)

    def test_sidebar_oculta_comentario_y_conserva_logout_post_con_csrf(self):
        respuesta = self.client.get(reverse('pos:punto_venta'))

        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, 'POST, no enlace')
        self.assertContains(
            respuesta,
            f'method="post" action="{reverse("usuarios:logout")}"',
        )
        self.assertContains(respuesta, 'name="csrfmiddlewaretoken"')

    def test_la_sesion_se_cierra_aunque_la_auditoria_falle(self):
        """
        La reproduccion de la auditoria: forzando el fallo del sink, `/logout/`
        respondia 500, conservaba la session key y la sesion seguia abriendo
        una vista autenticada. La auditoria es observabilidad, no un
        prerequisito de poder cerrar sesion.
        """
        with patch(
            'apps.usuarios.views.Auditoria.registrar',
            side_effect=RuntimeError('sink caido'),
        ):
            respuesta = self.client.post(reverse('usuarios:logout'))

        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(
            self.client.get(reverse('pos:punto_venta')).status_code, 302,
        )

    def test_el_login_no_revienta_si_la_auditoria_falla(self):
        self.client.logout()

        with patch(
            'apps.usuarios.views.Auditoria.registrar_login',
            side_effect=RuntimeError('sink caido'),
        ):
            respuesta = self.client.post(reverse('usuarios:login'), {
                'username': 'sale', 'password': 'Prueba123',
            })

        self.assertEqual(respuesta.status_code, 302)
        self.assertNotIn('login', respuesta['Location'])


class RedireccionDeLoginTests(UsuariosTestCase):
    """USR-008: `next` no puede sacar al usuario del sistema."""

    def setUp(self):
        super().setUp()
        self._usuario('viajero', rol='ADMIN')

    def _login(self, next_url):
        return self.client.post(
            reverse('usuarios:login') + f'?next={next_url}',
            {'username': 'viajero', 'password': 'Prueba123'},
        )

    def test_un_destino_externo_se_ignora(self):
        respuesta = self._login('https://sitio-externo.example/phish')

        self.assertEqual(respuesta.status_code, 302)
        self.assertNotIn('sitio-externo', respuesta['Location'])

    def test_un_destino_protocolo_relativo_se_ignora(self):
        respuesta = self._login('//sitio-externo.example/phish')

        self.assertNotIn('sitio-externo', respuesta['Location'])

    def test_un_destino_interno_si_se_honra(self):
        respuesta = self._login('/caja/')

        self.assertEqual(respuesta['Location'], '/caja/')

    def test_usuario_ya_autenticado_usa_el_mismo_home_por_rol(self):
        self.client.logout()
        cajera = self._usuario('cajera_home', rol='CAJERA')
        self.client.force_login(cajera)

        respuesta = self.client.get(reverse('usuarios:login'))

        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(respuesta['Location'], reverse('pos:punto_venta'))

    def test_login_local_unifica_last_login_y_ultimo_acceso(self):
        self._login('/caja/')
        usuario = User.objects.get(username='viajero')

        self.assertIsNotNone(usuario.last_login)
        self.assertEqual(usuario.ultimo_acceso, usuario.last_login)


class SesionAbsolutaTests(UsuariosTestCase):
    """USR-017: actividad continua no extiende la jornada mas de 12 horas."""

    @override_settings(SESSION_ABSOLUTE_MAX_AGE=12 * 60 * 60)
    def test_actividad_continua_no_supera_el_maximo_absoluto(self):
        usuario = self._usuario('jornada', rol='ADMIN')
        self.client.force_login(usuario)
        session = self.client.session
        session[SESSION_INICIO_ABSOLUTO] = 1_000.0
        session.save()

        with patch('apps.usuarios.middleware.time.time', return_value=44_201.0):
            respuesta = self.client.get(reverse('pos:punto_venta'))

        self.assertEqual(respuesta.status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_styleguide_exige_staff_aun_en_debug(self):
        usuario = self._usuario('sin_styleguide', rol='CAJERA')
        self.client.force_login(usuario)

        respuesta = self.client.get('/styleguide/')

        self.assertEqual(respuesta.status_code, 302)


class FrenoDeFuerzaBrutaTests(UsuariosTestCase):
    """USR-006: el login local tiene limite."""

    def setUp(self):
        super().setUp()
        self._usuario('victima', rol='ADMIN')

    def _intento(self, password='incorrecta'):
        return self.client.post(reverse('usuarios:login'), {
            'username': 'victima', 'password': password,
        })

    def test_una_rafaga_termina_bloqueada(self):
        """
        La reproduccion de la auditoria: doce passwords incorrectos devolvian
        la pantalla normal y no bloqueaban un login valido inmediato.
        """
        for _ in range(limite_login.rafaga_max):
            self.assertEqual(self._intento().status_code, 200)

        respuesta = self._intento()
        self.assertEqual(respuesta.status_code, 429)

    def test_estando_bloqueado_ni_la_credencial_correcta_entra(self):
        for _ in range(limite_login.rafaga_max):
            self._intento()

        respuesta = self._intento(password='Prueba123')

        self.assertEqual(respuesta.status_code, 429)
        self.assertFalse(respuesta.wsgi_request.user.is_authenticated)

    def test_un_login_exitoso_limpia_el_contador(self):
        for _ in range(limite_login.rafaga_max - 1):
            self._intento()

        self.assertEqual(self._intento(password='Prueba123').status_code, 302)

        self.client.post(reverse('usuarios:logout'))
        self.assertEqual(self._intento().status_code, 200)

    def test_el_bloqueo_no_alcanza_a_otro_usuario(self):
        """
        La clave combina IP y username: un atacante no puede dejar fuera a
        media instalacion machacando una sola cuenta.
        """
        self._usuario('otro', rol='ADMIN')
        for _ in range(limite_login.rafaga_max + 1):
            self._intento()

        respuesta = self.client.post(reverse('usuarios:login'), {
            'username': 'otro', 'password': 'Prueba123',
        })
        self.assertEqual(respuesta.status_code, 302)

    def test_el_bloqueo_no_revela_si_el_usuario_existe(self):
        for _ in range(limite_login.rafaga_max + 1):
            self._intento()

        inexistente = self.client.post(reverse('usuarios:login'), {
            'username': 'victima', 'password': 'x',
        })
        self.assertEqual(inexistente.status_code, 429)


class NegocioNoSeVuelveNuloTests(UsuariosTestCase):
    """USR-003: borrar un negocio no convierte a sus usuarios en globales."""

    def test_no_se_puede_borrar_un_negocio_con_usuarios(self):
        self._usuario('con_negocio')

        with self.assertRaises(ProtectedError):
            self.negocio.delete()

    def test_el_usuario_conserva_su_negocio(self):
        usuario = self._usuario('persistente')
        try:
            self.negocio.delete()
        except ProtectedError:
            pass

        usuario.refresh_from_db()
        self.assertEqual(usuario.negocio_id, self.negocio.id)

    def test_el_admin_expone_el_negocio(self):
        """
        El alta por admin omitia el campo por completo, asi que todo usuario
        creado desde ahi nacia con `negocio=NULL`.
        """
        from django.contrib import admin as django_admin

        from apps.usuarios.admin import UsuarioAdmin

        instancia = UsuarioAdmin(User, django_admin.site)
        campos_edicion = {
            campo
            for _, seccion in instancia.fieldsets
            for campo in seccion['fields']
        }
        campos_alta = {
            campo
            for _, seccion in instancia.add_fieldsets
            for campo in seccion['fields']
        }

        self.assertIn('negocio', campos_edicion)
        self.assertIn('negocio', campos_alta)
        self.assertFalse(instancia.has_add_permission(None))


class PuertaDeAdminTests(UsuariosTestCase):
    """USR-002: Admin no es una puerta paralela al portal."""

    def test_sin_tenancy_admin_sigue_siendo_del_instalador(self):
        usuario = self._usuario(
            'soporte_local', rol='SYSADMIN', is_staff=True, is_superuser=True,
        )
        self.client.force_login(usuario)

        self.assertEqual(self.client.get('/admin/').status_code, 200)

    def test_bajo_tenancy_hace_falta_identidad_global(self):
        from apps.usuarios.admin_site import _tiene_identidad_global

        usuario = self._usuario(
            'staff_sin_identidad', rol='SYSADMIN', is_staff=True, is_superuser=True,
        )

        self.assertFalse(_tiene_identidad_global(usuario))

    def test_una_identity_global_con_el_mismo_email_si_habilita(self):
        from apps.tenancy.models import Identity
        from apps.usuarios.admin_site import _tiene_identidad_global

        usuario = self._usuario(
            'operador_saas', rol='SYSADMIN', is_staff=True, is_superuser=True,
        )
        Identity.objects.create(
            email=usuario.email, nombre='Operador', is_global=True, activo=True,
        )

        self.assertTrue(_tiene_identidad_global(usuario))

    def test_una_identity_de_tenant_no_alcanza(self):
        from apps.tenancy.models import Identity
        from apps.usuarios.admin_site import _tiene_identidad_global

        usuario = self._usuario('duena_tenant', rol='ADMIN', is_staff=True)
        Identity.objects.create(
            email=usuario.email, nombre='Duena', is_global=False, activo=True,
        )

        self.assertFalse(_tiene_identidad_global(usuario))

    def test_una_identity_global_desactivada_tampoco(self):
        from apps.tenancy.models import Identity
        from apps.usuarios.admin_site import _tiene_identidad_global

        usuario = self._usuario('ex_operador', rol='SYSADMIN', is_staff=True)
        Identity.objects.create(
            email=usuario.email, nombre='Ex', is_global=True, activo=False,
        )

        self.assertFalse(_tiene_identidad_global(usuario))


class ManagerDeUsuariosTests(UsuariosTestCase):
    """USR-005: el instalador ya no puede crear el SYSADMIN sin email."""

    def test_create_superuser_sin_email_falla(self):
        """
        Lo que hacia `deploy/instalar.bat` fase 8. El fallo era correcto; el
        problema era que nadie miraba el codigo de salida y la instalacion
        declaraba exito igual.
        """
        with self.assertRaises(ValueError):
            User.objects.create_superuser(
                username='sysadmin_sin_email', email='', password='x',
            )

    def test_el_instalador_ya_no_pasa_email_vacio(self):
        import pathlib

        from django.conf import settings

        instalador = pathlib.Path(settings.BASE_DIR) / 'deploy' / 'instalar.bat'
        texto = instalador.read_text(encoding='utf-8', errors='ignore')

        self.assertNotIn("email=''", texto)
        # Y comprueba el codigo de salida en vez de seguir de largo.
        self.assertIn('errorlevel 1', texto)

    def test_payload_de_identidad_invalido_falla_en_el_manager(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            User.objects.create_user(
                username='rol_invalido', email='no-es-email', password='x',
                rol='OWNER', negocio=self.negocio,
            )

    def test_alta_humana_aplica_validadores_de_password(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            User.objects.create_human_user(
                username='humano', email='humano@example.com', password='1',
                rol='CAJERA', negocio=self.negocio,
            )

    def test_username_y_email_son_case_insensitive(self):
        from django.core.exceptions import ValidationError

        User.objects.create_user(
            username='CaseUser', email='Case@Example.com', password='x',
            negocio=self.negocio,
        )
        with self.assertRaises(ValidationError):
            User.objects.create_user(
                username='CASEUSER', email='otra@example.com', password='x',
                negocio=self.negocio,
            )
        self.assertIsNotNone(authenticate(username='CASEUSER', password='x'))

    def test_superuser_es_global_y_sysadmin_explicito(self):
        usuario = User.objects.create_superuser(
            username='root_explicito', email='root@example.com', password='x',
        )

        self.assertEqual(usuario.rol, 'SYSADMIN')
        self.assertIsNone(usuario.negocio_id)


class ProvisioningAutoritativoTests(UsuariosTestCase):
    """USR-007/010/013: alta usable, acotada y auditada."""

    def setUp(self):
        super().setUp()
        self.actor = self._usuario('admin_provision', rol='ADMIN')
        self.sucursal = Sucursal.objects.create(
            negocio=self.negocio, codigo='USR-01', nombre='Principal', activa=True,
        )
        self.rol = Rol.objects.create(
            negocio=self.negocio, nombre='Caja', slug='caja', activo=True,
        )

    def test_crea_usuario_asignacion_y_evento_en_la_misma_bd(self):
        usuario, asignacion = provisionar_usuario(
            actor=self.actor,
            negocio=self.negocio,
            username='Nueva.Cajera',
            email='Nueva.Cajera@Example.com',
            password='Una-Clave-Segura-2026!',
            rol=self.rol,
            sucursal=self.sucursal,
            canal=Auditoria.Canal.POS_LOCAL,
            using='default',
        )

        self.assertEqual(usuario.username, 'nueva.cajera')
        self.assertEqual(asignacion.usuario_id, usuario.pk)
        evento = Auditoria.objects.get(accion='usuarios.usuario.provisionado')
        self.assertEqual(evento.entity_ref is not None, True)
        self.assertEqual(
            evento.metadata['credential_policy'],
            'LOCAL_POS_SEPARATE_FROM_PORTAL',
        )
        self.assertNotIn('Una-Clave', str(evento.datos_nuevos))

    def test_rollback_de_auditoria_no_deja_primer_usuario_huerfano(self):
        with patch(
            'apps.usuarios.services.registrar_mutacion',
            side_effect=RuntimeError('sink'),
        ), self.assertRaises(RuntimeError):
            provisionar_usuario(
                actor=self.actor,
                negocio=self.negocio,
                username='rollback',
                email='rollback@example.com',
                password='Una-Clave-Segura-2026!',
                rol=self.rol,
                sucursal=self.sucursal,
                canal=Auditoria.Canal.POS_LOCAL,
                using='default',
            )

        self.assertFalse(User.objects.filter(username='rollback').exists())

    def test_actor_sin_permiso_no_puede_provisionar(self):
        actor = self._usuario('sin_permiso', rol='CAJERA')

        with self.assertRaisesMessage(ValueError, 'permisos.administrar'):
            provisionar_usuario(
                actor=actor,
                negocio=self.negocio,
                username='no-autorizado',
                email='no-autorizado@example.com',
                password='Una-Clave-Segura-2026!',
                rol=self.rol,
                sucursal=self.sucursal,
                canal=Auditoria.Canal.COMMAND,
                using='default',
            )

        self.assertFalse(User.objects.filter(username='no-autorizado').exists())
