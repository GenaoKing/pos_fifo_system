"""
apps/clientes/tests/test_auditoria_clientes.py

Regresion de los hallazgos de `docs/exploracion/AUDITORIA_CODIGO_APPS_CLIENTES.md`.

La app no tenia pruebas propias (CLI-020); este modulo es el arranque.
"""
import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.auditoria.models import Auditoria
from apps.clientes.models import Cliente
from apps.permisos import testing as permisos_testing

User = get_user_model()

LECTURA = ['clientes.ver']
EDICION = ['clientes.ver', 'clientes.editar']


class ClientesTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.negocio = permisos_testing.crear_negocio('Negocio CLI')
        self.cliente = Cliente.objects.create(
            nombre='Ferreteria Ramirez',
            tipo='CORPORATIVO',
            cedula_rnc='131-12345-6',
            telefono='809-555-1212',
            direccion='Calle Falsa 123',
            notas='Nota interna: paga tarde',
            limite_credito=Decimal('10000.00'),
            plazo_credito_dias=30,
            activo=True,
        )

    def tearDown(self):
        cache.clear()

    def _usuario(self, username, permisos=(), rol='CAJERA'):
        user = User.objects.create_user(
            username=username, email=f'{username}@test.local',
            password='Prueba123', rol=rol, activo=True,
        )
        permisos_testing.habilitar_cajero(user, permisos=list(permisos))
        return user

    def _api(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client


class LecturaGateadaTests(ClientesTestCase):
    """CLI-001: las lecturas locales exigen `clientes.ver`."""

    def test_sin_permiso_no_se_lista(self):
        """
        La reproduccion: un usuario autenticado sin ningun permiso recibia 200
        con cedula/RNC, telefono, direccion, notas internas, limite y saldo.
        """
        pelado = self._usuario('sin_ver', permisos=['ventas.crear'])
        self.client.force_login(pelado)

        respuesta = self.client.get(reverse('clientes:lista'))

        self.assertEqual(respuesta.status_code, 302)

    def test_sin_permiso_la_busqueda_devuelve_403(self):
        pelado = self._usuario('sin_buscar', permisos=['ventas.crear'])
        self.client.force_login(pelado)

        respuesta = self.client.get(
            reverse('clientes:api_buscar'), {'q': 'Ferreteria'},
        )

        self.assertEqual(respuesta.status_code, 403)
        self.assertNotIn(b'131-12345-6', respuesta.content)

    def test_sin_permiso_el_detalle_no_se_abre(self):
        pelado = self._usuario('sin_detalle', permisos=['ventas.crear'])
        self.client.force_login(pelado)

        respuesta = self.client.get(
            reverse('clientes:detalle', args=[self.cliente.id]),
        )

        self.assertEqual(respuesta.status_code, 302)

    def test_con_permiso_si_se_busca(self):
        autorizado = self._usuario('con_ver', permisos=LECTURA)
        self.client.force_login(autorizado)

        respuesta = self.client.get(
            reverse('clientes:api_buscar'), {'q': 'Ferreteria'},
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(len(respuesta.json()['clientes']), 1)


class CambioDeEstadoTests(ClientesTestCase):
    """CLI-002: activar/desactivar exige permiso y deja rastro."""

    def _toggle(self, user):
        self.client.force_login(user)
        return self.client.post(
            reverse('clientes:toggle', args=[self.cliente.id]),
        )

    def test_sin_permiso_no_se_desactiva(self):
        """
        La reproduccion: un usuario sin roles ni permisos desactivo un cliente
        y recibio 200, sin dejar ningun registro de auditoria.
        """
        pelado = self._usuario('sin_baja', permisos=['ventas.crear'])

        respuesta = self._toggle(pelado)

        self.assertEqual(respuesta.status_code, 403)
        self.cliente.refresh_from_db()
        self.assertTrue(self.cliente.activo)

    def test_con_permiso_se_desactiva_y_queda_auditado(self):
        autorizado = self._usuario(
            'con_baja', permisos=['clientes.ver', 'clientes.eliminar'],
        )

        respuesta = self._toggle(autorizado)

        self.assertEqual(respuesta.status_code, 200)
        self.cliente.refresh_from_db()
        self.assertFalse(self.cliente.activo)

        evento = Auditoria.objects.filter(
            descripcion__icontains='desactivado',
        ).first()
        self.assertIsNotNone(evento)
        self.assertEqual(evento.usuario_id, autorizado.id)
        self.assertEqual(evento.datos_anteriores, {'activo': True})
        self.assertEqual(evento.datos_nuevos, {'activo': False})

    def test_el_generico_no_se_desactiva(self):
        contado = Cliente.get_cliente_contado()
        autorizado = self._usuario(
            'con_baja2', permisos=['clientes.ver', 'clientes.eliminar'],
        )
        self.client.force_login(autorizado)

        respuesta = self.client.post(
            reverse('clientes:toggle', args=[contado.id]),
        )

        self.assertFalse(respuesta.json()['success'])
        contado.refresh_from_db()
        self.assertTrue(contado.activo)


class AtomicidadDeLaEdicionTests(ClientesTestCase):
    """CLI-005: editar, auditar y reprogramar son una sola operacion."""

    def setUp(self):
        super().setUp()
        self.editor = self._usuario(
            'editor', permisos=[*EDICION, 'clientes.editar_limite_credito'],
        )
        self.client.force_login(self.editor)

    def _editar(self, **cambios):
        datos = {
            'nombre': self.cliente.nombre,
            'tipo': self.cliente.tipo,
            'cedula_rnc': self.cliente.cedula_rnc,
            'limite_credito': str(self.cliente.limite_credito),
            'plazo_credito_dias': self.cliente.plazo_credito_dias,
            'activo': True,
        }
        datos.update(cambios)
        return self.client.post(
            reverse('clientes:editar', args=[self.cliente.id]),
            data=json.dumps(datos), content_type='application/json',
        )

    def test_un_fallo_de_auditoria_revierte_el_limite(self):
        """
        La reproduccion: forzando un fallo de `Auditoria.registrar`, la
        respuesta era 400 pero el nuevo limite ya estaba en base sin evidencia.
        """
        with patch(
            'apps.clientes.views.Auditoria.registrar',
            side_effect=RuntimeError('sink caido'),
        ):
            respuesta = self._editar(limite_credito='99999.00')

        self.assertEqual(respuesta.status_code, 400)
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.limite_credito, Decimal('10000.00'))

    def test_un_fallo_de_reprogramacion_revierte_el_plazo(self):
        """
        La otra mitad: el plazo quedaba confirmado con las cuotas viejas, es
        decir cliente y vencimientos en desacuerdo.
        """
        with patch(
            'apps.cuentas_por_cobrar.services.reprogramar_cxc_por_plazo_cliente',
            side_effect=RuntimeError('cartera caida'),
        ):
            respuesta = self._editar(plazo_credito_dias=90)

        self.assertEqual(respuesta.status_code, 400)
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.plazo_credito_dias, 30)

    def test_una_edicion_exitosa_confirma_y_audita(self):
        respuesta = self._editar(limite_credito='15000.00')

        self.assertEqual(respuesta.status_code, 200)
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.limite_credito, Decimal('15000.00'))
        self.assertTrue(
            Auditoria.objects.filter(descripcion__icontains='Limite de credito').exists()
        )

    def test_el_error_interno_no_llega_al_navegador(self):
        """CLI-014: `str(e)` iba literal en la respuesta."""
        with patch(
            'apps.clientes.views.Auditoria.registrar',
            side_effect=RuntimeError('detalle interno filtrable'),
        ):
            respuesta = self._editar(limite_credito='77777.00')

        self.assertNotIn('detalle interno filtrable', respuesta.json()['message'])


class LimiteDeCreditoPorApiTests(ClientesTestCase):
    """CLI-003: el limite exige su permiso tambien en el portal."""

    def _patch(self, user, payload):
        return self._api(user).patch(
            f'/api/v1/maestros/clientes/{self.cliente.id}/', payload, format='json',
        )

    def test_con_solo_clientes_editar_no_se_sube_el_limite(self):
        """
        La reproduccion: un usuario con `clientes.editar` pero sin
        `clientes.editar_limite_credito` elevo el limite por PATCH y recibio
        200, sin dejar auditoria financiera. Con eso se elude el override de
        credito: primero se sube el limite, despues se vende sin excepcion.
        """
        editor = self._usuario('api_editor', permisos=EDICION)

        respuesta = self._patch(editor, {'limite_credito': '999999.00'})

        self.assertEqual(respuesta.status_code, 403)
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.limite_credito, Decimal('10000.00'))

    def test_editar_el_contacto_si_pasa(self):
        editor = self._usuario('api_editor2', permisos=EDICION)

        respuesta = self._patch(editor, {'telefono': '809-000-0000'})

        self.assertEqual(respuesta.status_code, 200)

    def test_un_payload_mixto_tampoco_cuela_el_limite(self):
        """El caso que importa: mezclar un campo inocente con el financiero."""
        editor = self._usuario('api_editor3', permisos=EDICION)

        respuesta = self._patch(editor, {
            'telefono': '809-111-1111', 'limite_credito': '50000.00',
        })

        self.assertEqual(respuesta.status_code, 403)
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.telefono, '809-555-1212')

    def test_reenviar_el_mismo_limite_no_es_una_decision_financiera(self):
        editor = self._usuario('api_editor4', permisos=EDICION)

        respuesta = self._patch(editor, {
            'telefono': '809-222-2222',
            'limite_credito': str(self.cliente.limite_credito),
        })

        self.assertEqual(respuesta.status_code, 200)

    def test_con_el_permiso_financiero_si_se_sube(self):
        financiero = self._usuario(
            'api_financiero',
            permisos=[*EDICION, 'clientes.editar_limite_credito'],
        )

        respuesta = self._patch(financiero, {'limite_credito': '25000.00'})

        self.assertEqual(respuesta.status_code, 200)
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.limite_credito, Decimal('25000.00'))


class GenericoContadoTests(ClientesTestCase):
    """CLI-007: el generico es singleton, inmutable e imborrable."""

    def test_no_se_pueden_crear_dos_genericos(self):
        Cliente.get_cliente_contado()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Cliente.objects.create(tipo='CONTADO', nombre='OTRO CONTADO')

    def test_el_helper_es_estable(self):
        """
        Con dos duplicados exactos, `get_cliente_contado()` levantaba
        `MultipleObjectsReturned` y todo lo que lo llama devolvia 500.
        """
        primero = Cliente.get_cliente_contado()
        segundo = Cliente.get_cliente_contado()

        self.assertEqual(primero.pk, segundo.pk)

    def test_la_api_no_edita_el_generico(self):
        """
        `validate_tipo` solo corria si `tipo` venia en el payload: un PATCH
        parcial sobre la fila que YA es CONTADO la renombraba o desactivaba.
        """
        contado = Cliente.get_cliente_contado()
        admin = self._usuario('api_admin', rol='ADMIN')

        respuesta = self._api(admin).patch(
            f'/api/v1/maestros/clientes/{contado.id}/',
            {'nombre': 'RENOMBRADO'}, format='json',
        )

        self.assertEqual(respuesta.status_code, 403)
        contado.refresh_from_db()
        self.assertEqual(contado.nombre, 'CLIENTE CONTADO')

    def test_la_api_no_borra_el_generico(self):
        contado = Cliente.get_cliente_contado()
        admin = self._usuario('api_admin2', rol='ADMIN')

        respuesta = self._api(admin).delete(
            f'/api/v1/maestros/clientes/{contado.id}/'
        )

        self.assertEqual(respuesta.status_code, 403)
        self.assertTrue(Cliente.objects.filter(pk=contado.pk).exists())

    def test_un_cliente_real_si_se_borra(self):
        admin = self._usuario('api_admin3', rol='ADMIN')

        respuesta = self._api(admin).delete(
            f'/api/v1/maestros/clientes/{self.cliente.id}/'
        )

        self.assertEqual(respuesta.status_code, 204)


class AutoridadCloudTests(ClientesTestCase):
    """CLI-004: no confirmar localmente lo que el pull va a pisar."""

    def test_con_sync_activo_no_se_edita_un_cliente_del_cloud(self):
        """
        La reproduccion: se edito localmente un cliente ya adoptado y
        `_pull_clientes` restauro los valores del cloud, incluido el limite. La
        interfaz confirmaba una decision que desaparecia en el proximo pull.
        """
        self.cliente.origen_cloud_id = 4242
        self.cliente.save(update_fields=['origen_cloud_id'])

        editor = self._usuario('editor_local', permisos=EDICION)
        self.client.force_login(editor)

        with self.settings(SYNC_ENABLED=True):
            respuesta = self.client.post(
                reverse('clientes:editar', args=[self.cliente.id]),
                data=json.dumps({
                    'nombre': 'Nombre Local',
                    'tipo': 'CORPORATIVO',
                    'plazo_credito_dias': 30,
                }),
                content_type='application/json',
            )

        self.assertEqual(respuesta.status_code, 409)
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.nombre, 'Ferreteria Ramirez')

    def test_un_cliente_local_si_se_edita_con_sync_activo(self):
        """Los clientes creados en la sucursal siguen siendo suyos."""
        editor = self._usuario('editor_local2', permisos=EDICION)
        self.client.force_login(editor)

        with self.settings(SYNC_ENABLED=True):
            respuesta = self.client.post(
                reverse('clientes:editar', args=[self.cliente.id]),
                data=json.dumps({
                    'nombre': 'Nombre Nuevo',
                    'tipo': 'CORPORATIVO',
                    'plazo_credito_dias': 30,
                }),
                content_type='application/json',
            )

        self.assertEqual(respuesta.status_code, 200)

    def test_sin_sync_todo_es_local(self):
        self.cliente.origen_cloud_id = 4242
        self.cliente.save(update_fields=['origen_cloud_id'])

        editor = self._usuario('editor_standalone', permisos=EDICION)
        self.client.force_login(editor)

        respuesta = self.client.post(
            reverse('clientes:editar', args=[self.cliente.id]),
            data=json.dumps({
                'nombre': 'Nombre Standalone',
                'tipo': 'CORPORATIVO',
                'plazo_credito_dias': 30,
            }),
            content_type='application/json',
        )

        self.assertEqual(respuesta.status_code, 200)
