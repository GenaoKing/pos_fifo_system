"""
apps/auditoria/tests/test_auditoria_auditoria.py

Regresion de los hallazgos de `docs/exploracion/AUDITORIA_CODIGO_APPS_AUDITORIA.md`.

La app crítica no tenía pruebas propias (AUD-022); este módulo es el arranque.
"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.auditoria.models import Auditoria, AuditoriaInmutable, get_client_ip
from apps.permisos import testing as permisos_testing
from apps.sucursales.models import Sucursal

User = get_user_model()


class AuditoriaTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.negocio = permisos_testing.crear_negocio('Negocio AUD')
        self.suc_a = Sucursal.objects.create(
            codigo='AUD-A', nombre='Tienda A', activa=True, negocio=self.negocio,
        )
        self.suc_b = Sucursal.objects.create(
            codigo='AUD-B', nombre='Tienda B', activa=True, negocio=self.negocio,
        )

    def tearDown(self):
        cache.clear()

    def _usuario(self, username, rol='CAJERA'):
        user = User.objects.create_user(
            username=username, email=f'{username}@test.local',
            password='Prueba123', rol=rol, activo=True,
        )
        user.negocio = self.negocio
        user.save(update_fields=['negocio'])
        return user

    def _supervisor_de(self, sucursal, username='supervisor_aud'):
        usuario = self._usuario(username)
        permisos_testing.habilitar_cajero(
            usuario, permisos=['auditoria.ver'], sucursal=sucursal,
        )
        return usuario

    def _evento(self, descripcion, sucursal=None, usuario=None, **extra):
        return Auditoria.registrar(
            accion=Auditoria.TipoAccion.CREAR,
            descripcion=descripcion,
            usuario=usuario,
            sucursal=sucursal,
            **extra,
        )


class AlcanceDelHistorialTests(AuditoriaTestCase):
    """AUD-001: un permiso acotado a A no abre el historial de B."""

    def setUp(self):
        super().setUp()
        self._evento('Hecho de la tienda A', sucursal=self.suc_a)
        self._evento('Hecho de la tienda B', sucursal=self.suc_b)

    def test_la_api_no_devuelve_eventos_de_otra_sucursal(self):
        """
        La reproduccion de la auditoria: el usuario obtenia 200,
        `total_registros=2` y la descripcion de B.
        """
        supervisor = self._supervisor_de(self.suc_a)
        self.client.force_login(supervisor)

        datos = self.client.get(reverse('auditoria:api_buscar')).json()

        descripciones = [r['descripcion'] for r in datos['registros']]
        self.assertIn('Hecho de la tienda A', descripciones)
        self.assertNotIn('Hecho de la tienda B', descripciones)

    def test_las_estadisticas_tampoco_cuentan_la_otra(self):
        """
        El conteo agregado permite inferir la actividad de la otra tienda
        aunque despues se oculten las filas.
        """
        supervisor = self._supervisor_de(self.suc_a)
        self.client.force_login(supervisor)

        stats = self.client.get(
            reverse('auditoria:dashboard')
        ).context['init_data_json']['stats']

        self.assertEqual(stats['total_24h'], 1)

    def test_un_alcance_global_si_ve_todo(self):
        admin = self._usuario('admin_aud', rol='ADMIN')
        self.client.force_login(admin)

        datos = self.client.get(reverse('auditoria:api_buscar')).json()

        self.assertEqual(datos['paginacion']['total_registros'], 2)

    def test_un_consolidado_acotado_a_una_sucursal_no_consolida(self):
        usuario = self._usuario('falso_global_aud')
        permisos_testing.habilitar_cajero(
            usuario,
            permisos=['auditoria.ver', 'auditoria.consolidado.ver'],
            sucursal=self.suc_a,
        )
        self.client.force_login(usuario)

        datos = self.client.get(reverse('auditoria:api_buscar')).json()

        self.assertEqual(datos['paginacion']['total_registros'], 1)

    def test_sin_permiso_no_se_entra(self):
        pelado = self._usuario('sin_auditoria')
        permisos_testing.habilitar_cajero(pelado, permisos=['ventas.crear'])
        self.client.force_login(pelado)

        self.assertEqual(
            self.client.get(reverse('auditoria:api_buscar')).status_code, 403,
        )


class InmutabilidadTests(AuditoriaTestCase):
    """AUD-002: el historial es append-only y detecta alteraciones."""

    def test_un_registro_no_se_puede_modificar(self):
        """
        La reproduccion: una fila `CREATE / Original` se cambio por ORM a
        `DELETE / Alterado` y luego se elimino, sin impedimento.
        """
        evento = self._evento('Original')

        evento.descripcion = 'Alterado'
        with self.assertRaises(AuditoriaInmutable):
            evento.save()

    def test_un_registro_no_se_puede_borrar(self):
        evento = self._evento('Para borrar')

        with self.assertRaises(AuditoriaInmutable):
            evento.delete()

    def test_el_queryset_tampoco(self):
        self._evento('Masivo')

        with self.assertRaises(AuditoriaInmutable):
            Auditoria.objects.all().update(descripcion='pisado')

        with self.assertRaises(AuditoriaInmutable):
            Auditoria.objects.all().delete()

    def test_el_admin_no_ofrece_borrar(self):
        from django.contrib import admin as django_admin

        from apps.auditoria.admin import AuditoriaAdmin

        instancia = AuditoriaAdmin(Auditoria, django_admin.site)
        peticion = RequestFactory().get('/')
        peticion.user = self._usuario('root_aud', rol='SYSADMIN')
        peticion.user.is_superuser = True

        self.assertFalse(instancia.has_delete_permission(peticion))
        self.assertFalse(instancia.has_add_permission(peticion))
        self.assertFalse(instancia.has_change_permission(peticion))

    def test_una_alteracion_por_fuera_rompe_la_verificacion(self):
        """
        Lo que la aplicacion no puede impedir —un UPDATE en la base— si lo
        puede DETECTAR.
        """
        evento = self._evento('Genuino')
        self.assertTrue(evento.integridad_ok())

        # Por fuera del ORM del modelo, como lo haria alguien en psql.
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute(
                'UPDATE auditoria_auditoria SET descripcion = %s WHERE id = %s',
                ['Reescrito', evento.id],
            )

        evento.refresh_from_db()
        self.assertFalse(evento.integridad_ok())

    def test_el_hash_firma_el_instante_que_se_persiste(self):
        """
        Regresion del fallo de CI. `fecha_hora` era `auto_now_add`, que
        reescribe el campo con un `now()` nuevo DENTRO de `save()`, DESPUES de
        que el hash ya se firmo con el valor anterior. El instante firmado y el
        guardado diferian por microsegundos, asi que `integridad_ok()` de un
        registro intacto daba False. En Windows el reloj es grueso y ambos
        `now()` coincidian —el bug quedaba oculto—; en el Linux de CI divergian
        siempre.

        Dos aserciones: una de comportamiento (el hash guardado respeta el
        valor releido de la base) y una estructural, porque la primera vuelve a
        pasar por accidente en un host de reloj grueso. Lo que no puede volver
        es que el campo se reescriba al guardar.
        """
        evento = self._evento('Instante')
        evento.refresh_from_db()
        self.assertTrue(evento.integridad_ok())

        campo = Auditoria._meta.get_field('fecha_hora')
        self.assertFalse(
            getattr(campo, 'auto_now_add', False),
            'fecha_hora no puede ser auto_now_add: reescribe el valor despues '
            'de firmar el hash.',
        )
        self.assertFalse(getattr(campo, 'auto_now', False))

    def test_el_comando_reporta_la_alteracion(self):
        evento = self._evento('Genuino')
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute(
                'UPDATE auditoria_auditoria SET accion = %s WHERE id = %s',
                ['DELETE', evento.id],
            )

        with self.assertRaises(CommandError):
            call_command('verificar_auditoria', stdout=StringIO())

    def test_el_comando_pasa_con_historial_intacto(self):
        self._evento('Uno')
        self._evento('Dos')

        salida = StringIO()
        call_command('verificar_auditoria', stdout=salida)

        self.assertIn('Sin alteraciones', salida.getvalue())

    def test_la_purga_deja_constancia_de_si_misma(self):
        """
        Un historial que se puede vaciar sin dejar rastro del vaciado no es un
        historial.
        """
        viejo = self._evento('Antiguo')
        corte = timezone.now() + timedelta(days=1)

        borrados = Auditoria.objects.purgar_hasta(
            corte, motivo='Retencion de 90 dias',
        )

        self.assertGreaterEqual(borrados, 1)
        rastro = Auditoria.objects.filter(
            accion=Auditoria.TipoAccion.AUDITORIA_PURGADA,
        ).first()
        self.assertIsNotNone(rastro)
        self.assertIn('Retencion de 90 dias', rastro.descripcion)


class IdentidadDelActorTests(AuditoriaTestCase):
    """AUD-003: la identidad del actor se congela al momento del hecho."""

    def test_renombrar_al_usuario_no_reescribe_el_pasado(self):
        """
        La reproduccion: tras renombrar `audit_admin` a `actor_renombrado`, el
        evento antiguo pasaba a presentarse con el nombre nuevo.
        """
        usuario = self._usuario('audit_admin')
        evento = self._evento('Hecho', usuario=usuario)
        self.assertEqual(evento.actor_username, 'audit_admin')

        usuario.username = 'actor_renombrado'
        usuario.save(update_fields=['username'])

        evento.refresh_from_db()
        self.assertEqual(evento.actor_username, 'audit_admin')

    def test_una_cuenta_eliminada_no_se_presenta_como_sistema(self):
        """
        Con la FK nula, una accion humana se presentaba literalmente como
        "Sistema", igual que un job automatico.
        """
        usuario = self._usuario('efimero')
        evento = self._evento('Hecho humano', usuario=usuario)
        usuario.delete()

        evento.refresh_from_db()
        self.assertEqual(evento.actor_tipo, Auditoria.ACTOR_USUARIO)
        self.assertIn('efimero', evento.actor_display)
        self.assertIn('eliminada', evento.actor_display)

    def test_un_evento_del_sistema_si_dice_sistema(self):
        evento = self._evento('Job nocturno')

        self.assertEqual(evento.actor_tipo, Auditoria.ACTOR_SISTEMA)
        self.assertEqual(evento.actor_display, 'Sistema')

    def test_la_api_muestra_el_snapshot(self):
        usuario = self._usuario('cajero_visible')
        self._evento('Con actor', sucursal=self.suc_a, usuario=usuario)
        usuario.username = 'otro_nombre'
        usuario.save(update_fields=['username'])

        admin = self._usuario('admin_ver', rol='ADMIN')
        self.client.force_login(admin)
        datos = self.client.get(reverse('auditoria:api_buscar')).json()

        actores = [r['usuario'] for r in datos['registros']]
        self.assertNotIn('otro_nombre', actores)


class AtribucionDeSucursalTests(AuditoriaTestCase):
    """AUD-004: los helpers derivan la sucursal del objeto."""

    def test_registrar_venta_deriva_la_sucursal(self):
        """
        La reproduccion: se llamo `registrar_venta()` con un objeto cuya
        sucursal era A y el registro quedo con `sucursal_id = NULL`.
        """
        from apps.ventas.models import Venta

        usuario = self._usuario('vendedor')
        venta = Venta.objects.create(
            usuario=usuario, sucursal=self.suc_a,
            subtotal=Decimal('100.00'), total=Decimal('100.00'),
            estado='COMPLETADA', condicion_pago='CONTADO',
        )

        evento = Auditoria.registrar_venta(venta, usuario)

        self.assertEqual(evento.sucursal_id, self.suc_a.id)

    def test_la_anulacion_tambien(self):
        from apps.ventas.models import Venta

        usuario = self._usuario('anulador')
        venta = Venta.objects.create(
            usuario=usuario, sucursal=self.suc_b,
            subtotal=Decimal('50.00'), total=Decimal('50.00'),
            estado='ANULADA', condicion_pago='CONTADO',
        )

        evento = Auditoria.registrar_anulacion_venta(venta, usuario, 'Error de cobro')

        self.assertEqual(evento.sucursal_id, self.suc_b.id)

    def test_el_derivador_sigue_rutas_indirectas(self):
        class FalsoLote:
            sucursal = None

        class FalsoAjuste:
            lote = FalsoLote()

        FalsoLote.sucursal = self.suc_a
        self.assertEqual(Auditoria.derivar_sucursal(FalsoAjuste()), self.suc_a)

    def test_sin_objeto_no_inventa_sucursal(self):
        self.assertIsNone(Auditoria.derivar_sucursal(None))


class CoberturaPorNombreDeVistaTests(AuditoriaTestCase):
    """AUD-006: la cobertura no depende de substrings de URL."""

    def test_las_vistas_declaradas_existen(self):
        """
        El punto del hallazgo: la allowlist citaba rutas que ya no existian y
        nadie se enteraba. Ahora la cobertura se declara por nombre de vista, y
        este test falla si alguna deja de resolver.
        """
        from django.urls import NoReverseMatch, reverse

        from apps.auditoria.middleware import VISTAS_AUDITADAS

        faltantes = []
        for nombre in VISTAS_AUDITADAS:
            try:
                reverse(nombre)
            except NoReverseMatch as exc:
                # Una vista con argumentos resuelve igual si se le pasan; lo que
                # importa es que el NOMBRE exista en el urlconf.
                if 'not a valid view function or pattern name' in str(exc):
                    faltantes.append(nombre)

        self.assertEqual(
            faltantes, [],
            f'Vistas declaradas en VISTAS_AUDITADAS que ya no existen: {faltantes}',
        )

    def test_la_accion_no_se_adivina_del_metodo_http(self):
        """
        AUD-007: todo POST se registraba como `CREAR`, aunque la vista fuera
        una anulacion. El registro afirmaba algo que no habia pasado.
        """
        from apps.auditoria.middleware import VISTAS_AUDITADAS

        accion, nivel, _ = VISTAS_AUDITADAS['pos:api_anular_venta']
        self.assertEqual(accion, 'VENTA_CANCEL')
        self.assertEqual(nivel, 'CRITICA')

    def test_ya_no_se_decide_por_substring_de_url(self):
        import inspect

        from apps.auditoria import middleware

        fuente = inspect.getsource(middleware)
        self.assertNotIn('URLS_CRITICAS', fuente)
        self.assertIn('resolver_match', fuente)


class AtribucionDeIpTests(AuditoriaTestCase):
    """AUD-011: la IP no la elige el cliente."""

    def test_sin_proxy_declarado_se_ignora_x_forwarded_for(self):
        """
        `X-Forwarded-For` lo envia cualquiera: la version anterior lo prefería
        siempre, asi que el atacante escribia la IP de otro en su propio rastro.
        """
        peticion = RequestFactory().get(
            '/', HTTP_X_FORWARDED_FOR='1.2.3.4', REMOTE_ADDR='10.0.0.9',
        )

        self.assertEqual(get_client_ip(peticion), '10.0.0.9')

    @override_settings(AUDITORIA_CONFIAR_EN_PROXY=True)
    def test_con_proxy_declarado_se_toma_la_ultima_entrada(self):
        """
        La ultima la agrega el proxy de confianza; las anteriores las pudo
        haber puesto el cliente.
        """
        peticion = RequestFactory().get(
            '/', HTTP_X_FORWARDED_FOR='1.2.3.4, 10.0.0.7', REMOTE_ADDR='10.0.0.9',
        )

        self.assertEqual(get_client_ip(peticion), '10.0.0.7')


class ContratoHttpTests(AuditoriaTestCase):
    """AUD-014 y AUD-015."""

    def setUp(self):
        super().setUp()
        self.admin = self._usuario('admin_http', rol='ADMIN')
        self.client.force_login(self.admin)

    def test_una_pagina_no_numerica_no_es_500(self):
        respuesta = self.client.get(
            reverse('auditoria:api_buscar'), {'pagina': 'abc'},
        )

        self.assertEqual(respuesta.status_code, 200)

    def test_una_fecha_mal_formada_tampoco(self):
        respuesta = self.client.get(
            reverse('auditoria:api_buscar'), {'fecha_desde': 'ayer'},
        )

        self.assertEqual(respuesta.status_code, 200)

    def test_un_usuario_id_no_numerico_tampoco(self):
        respuesta = self.client.get(
            reverse('auditoria:api_buscar'), {'usuario_id': 'x'},
        )

        self.assertEqual(respuesta.status_code, 200)

    @override_settings(TIME_ZONE='America/Santo_Domingo', USE_TZ=True)
    def test_la_fecha_se_muestra_en_hora_local(self):
        """
        Se formateaba con `strftime` sobre el datetime en UTC: en Santo Domingo
        (UTC-4) todo el historial se leia cuatro horas corrido.
        """
        evento = self._evento('Con hora', sucursal=self.suc_a)

        datos = self.client.get(reverse('auditoria:api_buscar')).json()
        fila = next(r for r in datos['registros'] if r['id'] == evento.id)

        esperado = timezone.localtime(evento.fecha_hora).strftime('%d/%m/%Y %H:%M:%S')
        self.assertEqual(fila['fecha'], esperado)
        self.assertNotEqual(
            fila['fecha'], evento.fecha_hora.strftime('%d/%m/%Y %H:%M:%S'),
        )
