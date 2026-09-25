"""
Tests de `crear_config_inicial`.

CFG-015: sin `--sucursal`, el comando tomaba `.objects.first()` y la
sobrescribia AUNQUE estuviera ligada a una sucursal, etiquetando la salida
"sin sucursal - legacy". En una instalacion multi-sucursal, una instruccion
vieja pisaba la sucursal de menor PK en silencio.
"""
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.configuracion.models import ConfiguracionNegocio
from apps.sucursales.models import Sucursal


class CrearConfigInicialTests(TestCase):
    def test_sin_sucursal_con_configs_ligadas_aborta_sin_pisar(self):
        suc = Sucursal.objects.create(codigo='SD-1', nombre='S1', activa=True)
        ConfiguracionNegocio.objects.create(sucursal=suc, nombre_negocio='Uno')

        with self.assertRaises(CommandError):
            call_command('crear_config_inicial', '--nombre', 'Pisado')

        config = ConfiguracionNegocio.objects.get(sucursal=suc)
        self.assertEqual(config.nombre_negocio, 'Uno')  # intacta

    def test_con_sucursal_actualiza_la_de_esa_sucursal(self):
        suc = Sucursal.objects.create(codigo='SD-2', nombre='S2', activa=True)

        call_command('crear_config_inicial', '--sucursal', 'SD-2', '--nombre', 'Dos')

        config = ConfiguracionNegocio.objects.get(sucursal=suc)
        self.assertEqual(config.nombre_negocio, 'Dos')

    def test_legacy_sin_configs_crea_una_sin_sucursal(self):
        call_command('crear_config_inicial', '--nombre', 'Legacy')

        config = ConfiguracionNegocio.objects.get()
        self.assertIsNone(config.sucursal_id)
        self.assertEqual(config.nombre_negocio, 'Legacy')

    def test_varias_legacy_sin_sucursal_es_ambiguo(self):
        ConfiguracionNegocio.objects.create(sucursal=None, nombre_negocio='A')
        ConfiguracionNegocio.objects.create(sucursal=None, nombre_negocio='B')

        with self.assertRaises(CommandError):
            call_command('crear_config_inicial', '--nombre', 'C')
