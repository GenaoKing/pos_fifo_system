"""
El guard que convierte "migrate en verde sobre una base rota" en un fallo.

Regresion de BUG-F: `token_blacklist` quedo con 12 migraciones registradas y
cero tablas en las cuatro bases tenant de produccion, y `migrate` no tenia nada
que aplicar porque el registro decia que ya estaba. El login del portal murio
con `relation "token_blacklist_outstandingtoken" does not exist`.
"""
from unittest.mock import patch

from django.apps import apps as django_apps
from django.db import connections
from django.test import TestCase

from apps.tenancy.management.commands.migrate_tenants import tablas_faltantes
from apps.tenancy.router import CONTROL_PLANE_APPS, DEFAULT_ONLY_APPS


def _tablas_esperadas():
    excluidas = CONTROL_PLANE_APPS | DEFAULT_ONLY_APPS
    return {
        m._meta.db_table
        for m in django_apps.get_models()
        if m._meta.app_label not in excluidas and not m._meta.proxy and m._meta.managed
    }


class VerificacionDeTablasTenantTests(TestCase):
    def test_base_completa_no_reporta_nada(self):
        """Sin esto el guard seria ruido y alguien lo apagaria."""
        introspection = connections['default'].introspection
        with patch.object(introspection, 'table_names', return_value=list(_tablas_esperadas())):
            self.assertEqual(tablas_faltantes('default'), [])

    def test_tabla_ausente_se_reporta_con_modelo_y_tabla(self):
        faltante = 'token_blacklist_outstandingtoken'
        tablas = _tablas_esperadas()
        self.assertIn(faltante, tablas, 'el modelo del bug debe estar en el reparto tenant')

        introspection = connections['default'].introspection
        with patch.object(
            introspection, 'table_names', return_value=list(tablas - {faltante}),
        ):
            reportado = tablas_faltantes('default')

        self.assertEqual(len(reportado), 1)
        # El mensaje lleva modelo Y tabla: con solo uno de los dos no se sabe
        # que app hay que desregistrar de django_migrations para reparar.
        self.assertIn(faltante, reportado[0])
        self.assertIn('OutstandingToken', reportado[0])

    def test_los_modelos_del_control_plane_no_cuentan(self):
        """
        Las apps del control plane no viven en las bases tenant: exigirles tabla
        haria fallar todos los deploys.
        """
        introspection = connections['default'].introspection
        with patch.object(introspection, 'table_names', return_value=[]):
            reportado = ' '.join(tablas_faltantes('default'))

        for app_label in CONTROL_PLANE_APPS | DEFAULT_ONLY_APPS:
            self.assertNotIn(f'{app_label}.', reportado)
