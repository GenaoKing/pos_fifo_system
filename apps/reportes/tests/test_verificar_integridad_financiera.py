"""Regresión del preflight read-only previo a las constraints financieras."""
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.db import models
from django.test import TestCase


class VerificarIntegridadFinancieraTests(TestCase):
    def test_base_limpia_termina_verde(self):
        salida = StringIO()
        call_command('verificar_integridad_financiera', stdout=salida)
        self.assertIn('Integridad financiera OK', salida.getvalue())

    def test_violacion_reporta_modelo_y_falla(self):
        from apps.clientes.models import Cliente

        cliente = Cliente.objects.create(nombre='Preflight', tipo='CORPORATIVO')
        especificaciones = [
            (
                'clientes', 'Cliente', 'TEST-PREFLIGHT', 'fila seleccionada',
                models.Q(pk=cliente.pk),
            ),
        ]
        errores = StringIO()
        with (
            patch(
                'apps.reportes.management.commands.'
                'verificar_integridad_financiera.ESPECIFICACIONES',
                especificaciones,
            ),
            self.assertRaises(CommandError),
        ):
            call_command('verificar_integridad_financiera', stderr=errores)

        self.assertIn('TEST-PREFLIGHT clientes.Cliente', errores.getvalue())
