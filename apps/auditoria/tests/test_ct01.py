import uuid
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase, TransactionTestCase

from apps.auditoria.models import Auditoria
from apps.auditoria.services import (
    AuditContractError,
    evento_transportable,
    redactar_payload,
    registrar_mutacion,
)
from apps.negocios.models import Negocio
from apps.sucursales.models import Sucursal
from apps.tenancy.context import force_tenancy
from apps.tenancy.models import Identity, Tenant
from apps.usuarios.models import Usuario


class CT01TestCase(TestCase):
    def setUp(self):
        self.negocio = Negocio.objects.create(nombre='Contrato CT01', slug='ct01')
        self.sucursal = Sucursal.objects.create(
            codigo='CT-01', nombre='Principal', activa=True, negocio=self.negocio,
        )
        self.actor = Usuario.objects.create_user(
            username='auditor_ct01', email='auditor_ct01@example.com',
            password='Prueba123!', negocio=self.negocio, rol='ADMIN',
        )

    def _registrar(self, **extra):
        params = {
            'accion': 'negocio.identidad.actualizada',
            'actor': self.actor,
            'entidad': self.negocio,
            'antes': {'nombre': 'Anterior'},
            'despues': {'nombre': self.negocio.nombre},
            'resultado': Auditoria.Resultado.SUCCEEDED,
            'canal': Auditoria.Canal.POS_LOCAL,
            'tenant': self.negocio.slug,
            'sucursal': self.sucursal,
            'correlacion_id': uuid.uuid4(),
            'idempotencia_key': 'ct01-1',
            'metadata': {'reason': 'prueba'},
            'using': 'default',
        }
        params.update(extra)
        return registrar_mutacion(**params)

    def test_persiste_el_envelope_transportable_con_snapshots(self):
        evento = self._registrar()
        payload = evento_transportable(evento)

        self.assertEqual(payload['schema_version'], 'audit.event.v1')
        self.assertEqual(payload['tenant'], {'key': 'ct01'})
        self.assertEqual(payload['branch']['code_snapshot'], 'CT-01')
        self.assertEqual(payload['actor']['username_snapshot'], 'auditor_ct01')
        self.assertEqual(payload['entity']['type'], 'negocios.Negocio')
        self.assertEqual(payload['result'], 'SUCCEEDED')
        self.assertTrue(evento.integridad_ok())

    def test_renombrar_y_borrar_no_cambia_identidad_historica(self):
        evento = self._registrar()
        ref = evento.entity_ref
        display = evento.entity_display

        self.negocio.nombre = 'Renombrado'
        self.negocio.save(update_fields=['nombre'])
        evento.refresh_from_db()
        self.assertEqual(evento.entity_ref, ref)
        self.assertEqual(evento.entity_display, display)

    def test_redacta_secretos_en_diccionarios_listas_dsn_y_excepciones(self):
        evento = self._registrar(
            antes={
                'password': 'clarisimo',
                'nested': [{'Authorization': 'Bearer abc.def'}],
            },
            despues={'dsn': 'postgresql://user:clave@db/pos'},
            metadata={'nota': 'token=super-token'},
        )
        serializado = str(evento_transportable(evento))

        for secreto in ('clarisimo', 'abc.def', 'clave', 'super-token'):
            self.assertNotIn(secreto, serializado)
        self.assertIn('[REDACTED]', serializado)

        error = redactar_payload(RuntimeError('password=otra-clave'))
        self.assertNotIn('otra-clave', str(error))

    def test_si_falla_el_writer_la_mutacion_se_revierte(self):
        nombre_original = self.negocio.nombre
        with self.assertRaisesMessage(AuditContractError, 'AUDIT_WRITE_FAILED'):
            with transaction.atomic(using='default'):
                self.negocio.nombre = 'No debe quedar'
                self.negocio.save(update_fields=['nombre'])
                with patch.object(Auditoria, 'save', side_effect=RuntimeError('sink')):
                    self._registrar()

        self.negocio.refresh_from_db()
        self.assertEqual(self.negocio.nombre, nombre_original)

    def test_rollback_del_dominio_revierte_el_evento(self):
        event_id = None
        with self.assertRaises(RuntimeError):
            with transaction.atomic(using='default'):
                evento = self._registrar()
                event_id = evento.event_id
                raise RuntimeError('rollback del dominio')

        self.assertFalse(Auditoria.objects.filter(event_id=event_id).exists())

    def test_evento_fallido_puede_registrarse_despues_del_rollback(self):
        evento = self._registrar(
            resultado=Auditoria.Resultado.FAILED,
            error={'code': 'NEGOCIO_INVALIDO', 'message': 'password=no-filtrar'},
        )

        self.assertFalse(evento.exito)
        self.assertEqual(evento.error_codigo, 'NEGOCIO_INVALIDO')
        self.assertNotIn('no-filtrar', evento.mensaje_error)

    def test_control_plane_usa_default_sin_fk_a_usuario_tenant(self):
        tenant = Tenant.objects.create(
            tenant_key='control', slug='control', nombre='Control',
        )
        identity = Identity.objects.create(email='ops@example.com', is_global=True)
        with force_tenancy(True), transaction.atomic(using='default'):
            evento = registrar_mutacion(
                accion='tenant.provisioning.iniciado',
                actor=identity,
                entidad=tenant,
                antes={},
                despues={'estado': 'PENDING'},
                resultado=Auditoria.Resultado.SUCCEEDED,
                canal=Auditoria.Canal.COMMAND,
                using='default',
            )

        self.assertEqual(evento._state.db, 'default')
        self.assertIsNone(evento.usuario_id)
        self.assertEqual(evento.actor_username, 'ops@example.com')

    def test_taxonomia_y_resultados_incoherentes_se_rechazan(self):
        with self.assertRaises(ValidationError):
            Auditoria.objects.create(
                accion='FORGED_EVENT', descripcion='forjado',
                nivel_importancia='MEDIA',
            )
        with self.assertRaises(ValidationError):
            Auditoria.objects.create(
                accion=Auditoria.TipoAccion.CREAR,
                descripcion='resultado imposible',
                exito=True,
                mensaje_error='fallo',
            )


class CT01AtomicidadExplicitaTests(TransactionTestCase):
    reset_sequences = True

    def test_mutacion_exitosa_fuera_de_atomic_es_rechazada(self):
        negocio = Negocio.objects.create(nombre='Fuera atomic', slug='fuera-atomic')

        with self.assertRaisesMessage(AuditContractError, 'AUDIT_CONTEXT_INVALID'):
            registrar_mutacion(
                accion='negocio.identidad.actualizada',
                actor=None,
                entidad=negocio,
                antes={},
                despues={'nombre': negocio.nombre},
                resultado=Auditoria.Resultado.SUCCEEDED,
                canal=Auditoria.Canal.SYSTEM,
                tenant=negocio.slug,
                using='default',
            )
