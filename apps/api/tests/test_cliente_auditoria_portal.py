"""Las decisiones de crédito del portal dejan CT-01 en la BD del cliente."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.auditoria.models import Auditoria
from apps.clientes.models import Cliente


@override_settings(API_MAESTROS_PERMITE_ESCRITURA_LOCAL_TEST=True)
class AuditoriaClientePortalTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username='admin_cliente_a09', email='admin_cliente_a09@example.test',
            password='pass', rol='ADMIN', activo=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_alta_limite_y_baja_tienen_eventos_atomicos(self):
        creado = self.client.post('/api/v1/maestros/clientes/', {
            'tipo': 'PERSONAL', 'nombre': 'Cliente A09',
            'limite_credito': '100.00',
        }, format='json')
        self.assertEqual(creado.status_code, 201, creado.data)
        pk = creado.data['id']

        editado = self.client.patch(
            f'/api/v1/maestros/clientes/{pk}/',
            {'limite_credito': '200.00'}, format='json',
        )
        self.assertEqual(editado.status_code, 200, editado.data)
        baja = self.client.delete(f'/api/v1/maestros/clientes/{pk}/')
        self.assertEqual(baja.status_code, 200, baja.data)

        eventos = list(Auditoria.objects.filter(
            accion__startswith='clientes.cliente.', object_id=pk,
        ).order_by('id'))
        self.assertEqual([evento.accion for evento in eventos], [
            'clientes.cliente.creado',
            'clientes.cliente.limite_modificado',
            'clientes.cliente.desactivado',
        ])
        self.assertEqual(eventos[1].datos_anteriores['limite_credito'], '100.00')
        self.assertEqual(eventos[1].datos_nuevos['limite_credito'], '200.00')
        self.assertEqual(eventos[2].datos_nuevos['activo'], False)
        self.assertTrue(all(
            evento.actor_username == self.admin.username for evento in eventos
        ))

    def test_fallo_de_auditoria_revierte_limite(self):
        cliente = Cliente.objects.create(
            tipo='PERSONAL', nombre='Cliente rollback', limite_credito='100.00',
        )
        with patch('apps.api.views.maestros.registrar_mutacion',
                   side_effect=RuntimeError('audit down')):
            with self.assertRaises(RuntimeError):
                self.client.patch(
                    f'/api/v1/maestros/clientes/{cliente.pk}/',
                    {'limite_credito': '200.00'}, format='json',
                )

        cliente.refresh_from_db()
        self.assertEqual(str(cliente.limite_credito), '100.00')
        self.assertFalse(Auditoria.objects.filter(
            accion__startswith='clientes.cliente.', object_id=cliente.pk,
        ).exists())
