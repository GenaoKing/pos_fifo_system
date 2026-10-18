"""configuracion.0012: e-CF queda apagado y el pull vuelve a entregar la config."""
import importlib
from datetime import timedelta

from django.apps import apps as django_apps
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from apps.configuracion.models import ConfiguracionNegocio
from apps.sucursales.models import Sucursal


class ApagarEcfMigracionTests(TestCase):
    def test_apaga_el_flag_y_mueve_la_marca_del_pull(self):
        sucursal = Sucursal.objects.create(codigo='ECF-01', nombre='ECF', activa=True)
        config = ConfiguracionNegocio.objects.create(sucursal=sucursal, nombre_negocio='ECF')
        vieja = timezone.now() - timedelta(days=120)
        # Estado de Royal Plast: flag crudo encendido por el pull del 30/09.
        ConfiguracionNegocio.objects.filter(pk=config.pk).update(
            modulo_ecf=True, fecha_modificacion=vieja,
        )
        migracion = importlib.import_module(
            'apps.configuracion.migrations.0012_apagar_modulo_ecf'
        )

        with connection.schema_editor(atomic=False) as editor:
            migracion.apagar_ecf(django_apps, editor)

        config.refresh_from_db()
        self.assertFalse(config.modulo_ecf)
        self.assertGreater(config.fecha_modificacion, vieja)
