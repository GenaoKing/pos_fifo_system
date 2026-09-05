import sys
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.negocios.models import Negocio
from apps.permisos.models import AsignacionRol, Rol
from apps.sucursales.models import Sucursal
from apps.sync.models import EventoSync

from apps.notificaciones.catalogo import CAJA_CIERRE, CAJA_RETIRO, construir_desde_sync
from apps.notificaciones.models import (
    DestinatarioNotificacion,
    EntregaPush,
    EventoNotificable,
    EventoSyncNotificacionProcesado,
    ExcepcionNotificacionUsuario,
    MotorNotificaciones,
    ReglaNotificacionRol,
    SuscripcionPush,
)
from apps.notificaciones import push
from apps.notificaciones.push import ErrorEntregaPush
from apps.notificaciones.services import (
    REINTENTOS_MINUTOS,
    despachar_push,
    purgar_historial_si_corresponde,
    proyectar_pendientes,
    resolver_destinatarios,
)

Usuario = get_user_model()


class BaseNotificacionesTest(TestCase):
    def setUp(self):
        self.negocio = Negocio.objects.create(nombre='Demo', slug='demo')
        self.sucursal_a = Sucursal.objects.create(
            negocio=self.negocio, codigo='A', nombre='Sucursal A',
        )
        self.sucursal_b = Sucursal.objects.create(
            negocio=self.negocio, codigo='B', nombre='Sucursal B',
        )
        self.admin = Usuario.objects.create_user(
            'admin', 'admin@example.com', 'x', negocio=self.negocio, rol='ADMIN',
        )
        self.local = Usuario.objects.create_user(
            'local', 'local@example.com', 'x', negocio=self.negocio, rol='CAJERA',
        )
        self.rol_admin = Rol.objects.create(
            negocio=self.negocio, nombre='Administrador', slug='administrador',
            es_sistema=True,
        )
        self.rol_extra = Rol.objects.create(
            negocio=self.negocio, nombre='Supervisor', slug='supervisor',
        )
        AsignacionRol.objects.create(usuario=self.admin, rol=self.rol_admin)
        AsignacionRol.objects.create(
            usuario=self.local, rol=self.rol_admin, sucursal=self.sucursal_a,
        )

    def evento(self, *, tipo='CIERRE_CAJA', sucursal=None, payload=None, hash_payload='h1'):
        return EventoSync.objects.create(
            sucursal=sucursal or self.sucursal_a,
            tipo_evento=tipo,
            payload=payload or {},
            hash_payload=hash_payload,
            estado='CONFIRMADO',
            sent_at=timezone.now(),
            confirmed_at=timezone.now(),
        )


class ResolucionDestinatariosTests(BaseNotificacionesTest):
    def test_alcance_local_global_dedupe_y_exclusion(self):
        ReglaNotificacionRol.objects.create(
            rol=self.rol_admin, tipo_evento=CAJA_CIERRE,
        )
        ReglaNotificacionRol.objects.create(
            rol=self.rol_extra, tipo_evento=CAJA_CIERRE,
        )
        AsignacionRol.objects.create(usuario=self.admin, rol=self.rol_extra)

        datos = {'diferencia': '0.00'}
        usuarios_a = resolver_destinatarios(
            tipo_evento=CAJA_CIERRE, datos=datos, sucursal=self.sucursal_a,
        )
        self.assertEqual({r.usuario_id for r in usuarios_a}, {self.admin.id, self.local.id})
        self.assertEqual(len([r for r in usuarios_a if r.usuario_id == self.admin.id]), 1)

        usuarios_b = resolver_destinatarios(
            tipo_evento=CAJA_CIERRE, datos=datos, sucursal=self.sucursal_b,
        )
        self.assertEqual({r.usuario_id for r in usuarios_b}, {self.admin.id})

        ExcepcionNotificacionUsuario.objects.create(
            usuario=self.admin, tipo_evento=CAJA_CIERRE, modo='EXCLUIR',
        )
        self.assertEqual(
            resolver_destinatarios(
                tipo_evento=CAJA_CIERRE, datos=datos, sucursal=self.sucursal_b,
            ),
            [],
        )

    def test_inclusion_no_amplia_alcance_y_aplica_umbral(self):
        ExcepcionNotificacionUsuario.objects.create(
            usuario=self.local,
            tipo_evento=CAJA_RETIRO,
            modo='INCLUIR',
            parametros={'monto_minimo': '500.00'},
        )
        self.assertEqual(
            resolver_destinatarios(
                tipo_evento=CAJA_RETIRO,
                datos={'monto': '800.00'},
                sucursal=self.sucursal_b,
            ),
            [],
        )
        self.assertEqual(
            resolver_destinatarios(
                tipo_evento=CAJA_RETIRO,
                datos={'monto': '100.00'},
                sucursal=self.sucursal_a,
            ),
            [],
        )
        self.assertEqual(
            {r.usuario_id for r in resolver_destinatarios(
                tipo_evento=CAJA_RETIRO,
                datos={'monto': '800.00'},
                sucursal=self.sucursal_a,
            )},
            {self.local.id},
        )

    def test_cierre_siempre_llega_y_diferencia_superior_es_alerta(self):
        ReglaNotificacionRol.objects.create(
            rol=self.rol_admin,
            tipo_evento=CAJA_CIERRE,
            parametros={'umbral_diferencia': '10.00'},
        )
        normal = resolver_destinatarios(
            tipo_evento=CAJA_CIERRE,
            datos={'diferencia': '-10.00'},
            sucursal=self.sucursal_b,
        )
        alerta = resolver_destinatarios(
            tipo_evento=CAJA_CIERRE,
            datos={'diferencia': '-10.01'},
            sucursal=self.sucursal_b,
        )
        self.assertEqual(normal[0].nivel, 'NORMAL')
        self.assertEqual(alerta[0].nivel, 'ALERTA')

    def test_usuario_y_asignacion_inactivos_no_reciben(self):
        ReglaNotificacionRol.objects.create(
            rol=self.rol_admin, tipo_evento=CAJA_CIERRE,
        )
        self.local.activo = False
        self.local.save(update_fields=['activo'])
        usuarios = resolver_destinatarios(
            tipo_evento=CAJA_CIERRE,
            datos={'diferencia': '0.00'},
            sucursal=self.sucursal_a,
        )
        self.assertEqual({item.usuario_id for item in usuarios}, {self.admin.id})

    def test_inclusion_no_quita_lo_que_el_rol_concede(self):
        # El rol notifica todos los retiros; el INCLUIR del usuario tiene un
        # umbral mas alto. Un retiro por debajo de ese umbral NO debe quitar al
        # usuario: la inclusion es aditiva, nunca resta lo que el rol dio.
        ReglaNotificacionRol.objects.create(
            rol=self.rol_admin, tipo_evento=CAJA_RETIRO,
            enviar_push=False, parametros={'monto_minimo': '0.00'},
        )
        ExcepcionNotificacionUsuario.objects.create(
            usuario=self.local, tipo_evento=CAJA_RETIRO, modo='INCLUIR',
            enviar_push=True, parametros={'monto_minimo': '5000.00'},
        )
        resueltos = resolver_destinatarios(
            tipo_evento=CAJA_RETIRO,
            datos={'monto': '1000.00'},
            sucursal=self.sucursal_a,
        )
        por_usuario = {r.usuario_id: r for r in resueltos}
        self.assertIn(self.local.id, por_usuario)
        # Conserva el push del ROL (False), porque el INCLUIR no aplico.
        self.assertFalse(por_usuario[self.local.id].enviar_push)

    def test_inclusion_que_aplica_gana_al_rol(self):
        ReglaNotificacionRol.objects.create(
            rol=self.rol_admin, tipo_evento=CAJA_RETIRO,
            enviar_push=False, parametros={'monto_minimo': '0.00'},
        )
        ExcepcionNotificacionUsuario.objects.create(
            usuario=self.local, tipo_evento=CAJA_RETIRO, modo='INCLUIR',
            enviar_push=True, parametros={'monto_minimo': '5000.00'},
        )
        resueltos = resolver_destinatarios(
            tipo_evento=CAJA_RETIRO,
            datos={'monto': '6000.00'},
            sucursal=self.sucursal_a,
        )
        por_usuario = {r.usuario_id: r for r in resueltos}
        self.assertIn(self.local.id, por_usuario)
        # El INCLUIR aplico (6000 >= 5000): impone su push (True) sobre el rol.
        self.assertTrue(por_usuario[self.local.id].enviar_push)

    def test_sucursal_o_negocio_inactivos_siguen_al_engine(self):
        # Espejo de permisos.engine: una sucursal inactiva no honra la
        # asignacion acotada, pero la global sigue; un negocio inactivo corta
        # todo. Sin esto el motor generaria destinatarios que el RBAC ya niega.
        ReglaNotificacionRol.objects.create(
            rol=self.rol_admin, tipo_evento=CAJA_CIERRE,
        )
        datos = {'diferencia': '0.00'}
        self.sucursal_a.activa = False
        self.sucursal_a.save(update_fields=['activa'])
        solo_global = resolver_destinatarios(
            tipo_evento=CAJA_CIERRE, datos=datos, sucursal=self.sucursal_a,
        )
        # 'local' esta acotado a sucursal_a (ahora inactiva); 'admin' es global.
        self.assertEqual({r.usuario_id for r in solo_global}, {self.admin.id})

        self.negocio.activo = False
        self.negocio.save(update_fields=['activo'])
        self.assertEqual(
            resolver_destinatarios(
                tipo_evento=CAJA_CIERRE, datos=datos, sucursal=self.sucursal_a,
            ),
            [],
        )


class ProyeccionTests(BaseNotificacionesTest):
    def test_cierre_legacy_se_completa_como_estimado(self):
        evento = self.evento(
            hash_payload='legacy',
            payload={
                'fecha_apertura': (timezone.now() - timedelta(hours=1)).isoformat(),
                'fecha_cierre': timezone.now().isoformat(),
                'monto_esperado': '100.00',
                'monto_contado': '99.00',
                'diferencia': '-1.00',
            },
        )
        construido = construir_desde_sync(evento)
        self.assertEqual(construido['datos']['fuente_resumen'], 'cloud_estimado')
        self.assertEqual(construido['datos']['diferencia'], '-1.00')

    def test_corte_desde_ahora_e_idempotencia(self):
        ReglaNotificacionRol.objects.create(
            rol=self.rol_admin, tipo_evento='caja.apertura',
        )
        corte = timezone.now()
        motor = MotorNotificaciones.actual()
        motor.activo = True
        motor.activado_desde = corte
        motor.save()
        self.evento(
            tipo='APERTURA_CAJA',
            hash_payload='antiguo',
            payload={'fecha_apertura': corte.isoformat(), 'fondo_apertura': '100.00'},
        )
        EventoSync.objects.filter(hash_payload='antiguo').update(
            confirmed_at=corte - timedelta(seconds=1),
        )
        self.evento(
            tipo='APERTURA_CAJA',
            hash_payload='nuevo',
            payload={'fecha_apertura': corte.isoformat(), 'fondo_apertura': '100.00'},
        )

        primero = proyectar_pendientes()
        segundo = proyectar_pendientes()
        self.assertEqual(
            primero,
            {'procesados': 1, 'generados': 1, 'errores_proyeccion': 0},
        )
        self.assertEqual(
            segundo,
            {'procesados': 0, 'generados': 0, 'errores_proyeccion': 0},
        )
        self.assertEqual(EventoNotificable.objects.count(), 1)
        self.assertEqual(DestinatarioNotificacion.objects.count(), 2)

    def test_dos_dispositivos_crean_dos_entregas_y_una_fila_de_bandeja(self):
        ReglaNotificacionRol.objects.create(
            rol=self.rol_admin, tipo_evento=CAJA_CIERRE,
        )
        for numero in (1, 2):
            SuscripcionPush.objects.create(
                usuario=self.admin,
                endpoint=f'https://push.example/{numero}',
                p256dh='p',
                auth='a',
            )
        motor = MotorNotificaciones.actual()
        motor.activo = True
        motor.activado_desde = timezone.now() - timedelta(seconds=1)
        motor.save()
        self.evento(
            hash_payload='dos-dispositivos',
            payload={
                'fecha_cierre': timezone.now().isoformat(),
                'resumen_turno': {'diferencia': '0.00'},
            },
        )

        proyectar_pendientes()

        bandeja_admin = DestinatarioNotificacion.objects.filter(usuario=self.admin)
        self.assertEqual(bandeja_admin.count(), 1)
        self.assertEqual(
            EntregaPush.objects.filter(destinatario__in=bandeja_admin).count(),
            2,
        )

    def test_purga_conserva_marcador_de_evento_sync(self):
        fuente = self.evento(hash_payload='purga')
        marcador = EventoSyncNotificacionProcesado.objects.create(
            evento_sync=fuente,
            genero_evento=True,
        )
        EventoNotificable.objects.create(
            tipo_evento=CAJA_CIERRE,
            clave_fuente='purga',
            sucursal=self.sucursal_a,
            titulo='Antigua',
            cuerpo='Antigua',
            ocurrido_en=timezone.now() - timedelta(days=91),
        )

        self.assertEqual(purgar_historial_si_corresponde(), 1)

        self.assertFalse(
            EventoNotificable.objects.filter(clave_fuente='purga').exists()
        )
        self.assertTrue(
            EventoSyncNotificacionProcesado.objects.filter(pk=marcador.pk).exists()
        )

    def _activar_motor(self):
        motor = MotorNotificaciones.actual()
        motor.activo = True
        motor.activado_desde = timezone.now() - timedelta(seconds=1)
        motor.save()
        return motor

    def test_motor_apagado_devuelve_forma_completa(self):
        # El motor nace apagado; el resumen debe traer las tres claves igual
        # que el camino normal, para que ejecutar_ciclo no pierda la clave.
        self.assertEqual(
            proyectar_pendientes(),
            {'procesados': 0, 'generados': 0, 'errores_proyeccion': 0},
        )

    def test_proyeccion_que_falla_reintenta_y_luego_muere(self):
        self._activar_motor()
        evento = self.evento(
            tipo='APERTURA_CAJA', hash_payload='malo',
            payload={'fondo_apertura': '10.00'},
        )
        with patch(
            'apps.notificaciones.services.construir_desde_sync',
            side_effect=ValueError('payload roto'),
        ):
            primero = proyectar_pendientes()
            self.assertEqual(primero['errores_proyeccion'], 1)
            self.assertEqual(primero['procesados'], 0)
            marcador = EventoSyncNotificacionProcesado.objects.get(
                evento_sync=evento,
            )
            self.assertEqual(
                marcador.estado, EventoSyncNotificacionProcesado.REINTENTO,
            )
            self.assertEqual(marcador.intentos, 1)
            self.assertIsNotNone(marcador.proximo_intento_en)

            # Antes de que venza el proximo intento, no se reselecciona.
            segundo = proyectar_pendientes()
            self.assertEqual(segundo['procesados'], 0)
            self.assertEqual(segundo['errores_proyeccion'], 0)

            # Agotar la escalera y vencer el reintento: pasa a FALLIDO.
            EventoSyncNotificacionProcesado.objects.filter(pk=marcador.pk).update(
                intentos=len(REINTENTOS_MINUTOS),
                proximo_intento_en=timezone.now() - timedelta(seconds=1),
            )
            proyectar_pendientes()
            marcador.refresh_from_db()
            self.assertEqual(
                marcador.estado, EventoSyncNotificacionProcesado.FALLIDO,
            )
            # Un FALLIDO ya no se selecciona en ciclos siguientes.
            self.assertEqual(proyectar_pendientes()['procesados'], 0)

    def test_reintento_exitoso_cierra_el_tombstone(self):
        ReglaNotificacionRol.objects.create(
            rol=self.rol_admin, tipo_evento='caja.apertura',
        )
        self._activar_motor()
        evento = self.evento(
            tipo='APERTURA_CAJA', hash_payload='recupera',
            payload={
                'fecha_apertura': timezone.now().isoformat(),
                'fondo_apertura': '100.00',
            },
        )
        # Como si un intento previo hubiera fallado y ya venciera el reintento.
        EventoSyncNotificacionProcesado.objects.create(
            evento_sync=evento,
            estado=EventoSyncNotificacionProcesado.REINTENTO,
            intentos=1,
            proximo_intento_en=timezone.now() - timedelta(seconds=1),
        )
        resultado = proyectar_pendientes()
        self.assertEqual(resultado['procesados'], 1)
        self.assertEqual(resultado['generados'], 1)
        marcador = EventoSyncNotificacionProcesado.objects.get(evento_sync=evento)
        self.assertEqual(marcador.estado, EventoSyncNotificacionProcesado.PROCESADO)
        self.assertTrue(marcador.genero_evento)
        self.assertIsNone(marcador.proximo_intento_en)
        self.assertEqual(EventoNotificable.objects.count(), 1)

    def test_purga_drena_en_lotes_sin_esperar_un_dia(self):
        for numero in range(3):
            EventoNotificable.objects.create(
                tipo_evento=CAJA_CIERRE, clave_fuente=f'viejo-{numero}',
                sucursal=self.sucursal_a, titulo='Antigua', cuerpo='Antigua',
                ocurrido_en=timezone.now() - timedelta(days=91),
            )
        with patch('apps.notificaciones.services.PURGA_LOTE', 2):
            # Lote lleno (2 de 2): borra 2 y NO cierra la ventana de 24h.
            self.assertEqual(purgar_historial_si_corresponde(), 2)
            self.assertIsNone(MotorNotificaciones.actual().ultima_purga)
            # Queda 1: lote no lleno -> drena y recien ahi estampa ultima_purga.
            self.assertEqual(purgar_historial_si_corresponde(), 1)
            self.assertIsNotNone(MotorNotificaciones.actual().ultima_purga)


@override_settings(
    WEB_PUSH_ENABLED=True,
    WEB_PUSH_VAPID_PUBLIC_KEY='publica',
    WEB_PUSH_VAPID_PRIVATE_KEY='privada',
)
class EntregaPushTests(BaseNotificacionesTest):
    def setUp(self):
        super().setUp()
        evento = EventoNotificable.objects.create(
            tipo_evento=CAJA_CIERRE,
            clave_fuente='uno',
            sucursal=self.sucursal_a,
            titulo='Cierre',
            cuerpo='Resumen',
            ocurrido_en=timezone.now(),
        )
        destinatario = DestinatarioNotificacion.objects.create(
            evento=evento, usuario=self.admin,
        )
        suscripcion = SuscripcionPush.objects.create(
            usuario=self.admin,
            endpoint='https://push.example/1',
            p256dh='p', auth='a',
        )
        self.entrega = EntregaPush.objects.create(
            destinatario=destinatario, suscripcion=suscripcion,
        )

    @patch('apps.notificaciones.services.push.enviar')
    def test_410_desactiva_dispositivo(self, enviar):
        enviar.side_effect = ErrorEntregaPush('WebPushException HTTP 410', status_code=410)
        resultado = despachar_push()
        self.entrega.refresh_from_db()
        self.entrega.suscripcion.refresh_from_db()
        self.assertEqual(resultado['descartadas'], 1)
        self.assertEqual(self.entrega.estado, EntregaPush.DESCARTADA)
        self.assertFalse(self.entrega.suscripcion.activa)

    @patch('apps.notificaciones.services.push.enviar')
    def test_410_descarta_el_resto_de_la_cola_del_dispositivo(self, enviar):
        otro_evento = EventoNotificable.objects.create(
            tipo_evento=CAJA_CIERRE,
            clave_fuente='dos',
            sucursal=self.sucursal_a,
            titulo='Otro cierre',
            cuerpo='Resumen',
            ocurrido_en=timezone.now(),
        )
        otro_destinatario = DestinatarioNotificacion.objects.create(
            evento=otro_evento,
            usuario=self.admin,
        )
        otra = EntregaPush.objects.create(
            destinatario=otro_destinatario,
            suscripcion=self.entrega.suscripcion,
        )
        enviar.side_effect = ErrorEntregaPush('HTTP 410', status_code=410)

        despachar_push()

        otra.refresh_from_db()
        self.assertEqual(otra.estado, EntregaPush.DESCARTADA)

    @patch('apps.notificaciones.services.push.enviar')
    def test_lease_vencido_se_recupera(self, enviar):
        self.entrega.estado = EntregaPush.EN_PROCESO
        self.entrega.lease_hasta = timezone.now() - timedelta(seconds=1)
        self.entrega.save(update_fields=['estado', 'lease_hasta'])

        resultado = despachar_push()

        self.entrega.refresh_from_db()
        self.assertEqual(resultado['enviadas'], 1)
        self.assertEqual(self.entrega.estado, EntregaPush.ENVIADA)
        self.assertIsNone(self.entrega.lease_hasta)

    @patch('apps.notificaciones.services.push.enviar')
    def test_429_programa_primer_reintento(self, enviar):
        enviar.side_effect = ErrorEntregaPush(
            'WebPushException HTTP 429', status_code=429, reintentable=True,
        )
        antes = timezone.now()
        resultado = despachar_push()
        self.entrega.refresh_from_db()
        self.assertEqual(resultado['reintentadas'], 1)
        self.assertEqual(self.entrega.intentos, 1)
        self.assertGreaterEqual(
            self.entrega.proximo_intento_en, antes + timedelta(seconds=50),
        )

    def test_adaptador_extrae_410_desde_response_sin_filtrar_payload(self):
        class WebPushException(Exception):
            def __init__(self):
                super().__init__('endpoint secreto y cuerpo monetario')
                self.response = SimpleNamespace(status_code=410)

        modulo = SimpleNamespace(
            WebPushException=WebPushException,
            webpush=lambda **kwargs: (_ for _ in ()).throw(WebPushException()),
        )
        with patch.dict(sys.modules, {'pywebpush': modulo}):
            with self.assertRaises(ErrorEntregaPush) as contexto:
                push.enviar(self.entrega.suscripcion, self.entrega.destinatario)
        self.assertEqual(contexto.exception.status_code, 410)
        self.assertFalse(contexto.exception.reintentable)
        self.assertEqual(str(contexto.exception), 'WebPushException HTTP 410')


class ApiNotificacionesTests(BaseNotificacionesTest):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        evento = EventoNotificable.objects.create(
            tipo_evento=CAJA_CIERRE, clave_fuente='api',
            sucursal=self.sucursal_a, titulo='Cierre', cuerpo='RD$100.00',
            datos={'diferencia': '0.00'}, ocurrido_en=timezone.now(),
        )
        self.propia = DestinatarioNotificacion.objects.create(
            evento=evento, usuario=self.admin,
        )
        self.ajena = DestinatarioNotificacion.objects.create(
            evento=evento, usuario=self.local,
        )

    def test_bandeja_y_lectura_solo_del_propietario(self):
        response = self.client.get('/api/v1/notificaciones/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], self.propia.id)
        response = self.client.post(
            f'/api/v1/notificaciones/{self.ajena.id}/marcar-leida/',
        )
        self.assertEqual(response.status_code, 404)

    def test_alta_push_idempotente_y_baja_propia(self):
        payload = {
            'endpoint': 'https://push.example/api',
            'keys': {'p256dh': 'p', 'auth': 'a'},
            'nombre_dispositivo': 'Edge personal',
        }
        self.assertEqual(
            self.client.post(
                '/api/v1/notificaciones/push/suscripciones/', payload,
                format='json',
            ).status_code,
            201,
        )
        response = self.client.post(
            '/api/v1/notificaciones/push/suscripciones/', payload, format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SuscripcionPush.objects.count(), 1)
        pk = response.data['id']
        self.assertEqual(
            self.client.delete(
                f'/api/v1/notificaciones/push/suscripciones/{pk}/',
            ).status_code,
            204,
        )
        self.assertFalse(SuscripcionPush.objects.get(pk=pk).activa)

    def test_crud_regla_valida_dinero_y_negocio(self):
        payload = {
            'destinatario_tipo': 'ROL',
            'rol': self.rol_admin.id,
            'tipo_evento': CAJA_CIERRE,
            'activa': True,
            'enviar_push': True,
            'parametros': {'umbral_diferencia': '25.00'},
        }
        response = self.client.post(
            '/api/v1/notificaciones/reglas/', payload, format='json',
        )
        self.assertEqual(response.status_code, 201)
        rule_id = response.data['id']
        self.assertEqual(response.data['parametros']['umbral_diferencia'], '25.00')

        response = self.client.patch(
            f'/api/v1/notificaciones/reglas/{rule_id}/',
            {'activa': False}, format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['activa'])

        payload['parametros'] = {'umbral_diferencia': '-0.01'}
        response = self.client.post(
            '/api/v1/notificaciones/reglas/', payload, format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_alta_push_transfiere_dispositivo_compartido(self):
        # El endpoint es estable por navegador: si otra cuenta lo registro en
        # el mismo equipo, el dispositivo se transfiere en vez de rechazarse.
        payload = {
            'endpoint': 'https://push.example/compartido',
            'keys': {'p256dh': 'p', 'auth': 'a'},
        }
        self.assertEqual(
            self.client.post(
                '/api/v1/notificaciones/push/suscripciones/', payload,
                format='json',
            ).status_code,
            201,
        )
        suscripcion = SuscripcionPush.objects.get(endpoint=payload['endpoint'])
        self.assertEqual(suscripcion.usuario_id, self.admin.id)
        entrega = EntregaPush.objects.create(
            destinatario=self.propia, suscripcion=suscripcion,
        )

        otro = APIClient()
        otro.force_authenticate(self.local)
        response = otro.post(
            '/api/v1/notificaciones/push/suscripciones/', payload, format='json',
        )
        self.assertEqual(response.status_code, 201)

        self.assertEqual(
            SuscripcionPush.objects.filter(endpoint=payload['endpoint']).count(),
            1,
        )
        suscripcion.refresh_from_db()
        self.assertEqual(suscripcion.usuario_id, self.local.id)
        entrega.refresh_from_db()
        self.assertEqual(entrega.estado, EntregaPush.DESCARTADA)
        # Admin ya no ve el dispositivo transferido.
        listado = self.client.get('/api/v1/notificaciones/push/suscripciones/')
        self.assertEqual(listado.status_code, 200)
        self.assertEqual(
            [d for d in listado.data if d['endpoint'] == payload['endpoint']], [],
        )

    def test_listar_reglas_no_escala_con_el_numero_de_reglas(self):
        # El conteo de push por regla se hace en batch: las queries del listado
        # no deben crecer al agregar mas reglas/excepciones (sin N+1).
        ReglaNotificacionRol.objects.create(
            rol=self.rol_admin, tipo_evento=CAJA_CIERRE,
        )
        ExcepcionNotificacionUsuario.objects.create(
            usuario=self.local, tipo_evento=CAJA_CIERRE, modo='INCLUIR',
        )
        with CaptureQueriesContext(connection) as pocas:
            self.client.get('/api/v1/notificaciones/reglas/')

        ReglaNotificacionRol.objects.create(
            rol=self.rol_extra, tipo_evento=CAJA_RETIRO,
        )
        ExcepcionNotificacionUsuario.objects.create(
            usuario=self.admin, tipo_evento=CAJA_RETIRO, modo='EXCLUIR',
        )
        with CaptureQueriesContext(connection) as muchas:
            self.client.get('/api/v1/notificaciones/reglas/')

        self.assertEqual(len(pocas), len(muchas))
