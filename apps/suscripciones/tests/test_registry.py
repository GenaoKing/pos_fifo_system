"""Tests del registro de modulos y el grafo de dependencias (sin DB)."""
from django.test import SimpleTestCase

from apps.suscripciones import registry


class RegistryTests(SimpleTestCase):
    def test_cierre_incluye_dependencias_transitivas(self):
        # cuentas_por_cobrar -> ventas, clientes; ventas -> productos, inventario
        self.assertEqual(
            registry.cierre_dependencias(['cuentas_por_cobrar']),
            {'cuentas_por_cobrar', 'ventas', 'clientes', 'productos', 'inventario'},
        )

    def test_dependientes_de_ventas(self):
        deps = registry.dependientes_de('ventas')
        self.assertIn('cuentas_por_cobrar', deps)
        self.assertIn('ecf', deps)
        self.assertIn('caja', deps)
        self.assertNotIn('ventas', deps)
        self.assertNotIn('productos', deps)  # productos no depende de ventas

    def test_core_keys(self):
        core = registry.core_keys()
        self.assertIn('ventas', core)
        self.assertIn('inventario', core)
        self.assertNotIn('ecf', core)
        self.assertNotIn('cuentas_por_cobrar', core)

    def test_validar_detecta_desconocidos(self):
        self.assertEqual(registry.validar(['ecf', 'inexistente']), {'inexistente'})

    def test_financiacion_depende_solo_de_ventas(self):
        self.assertEqual(
            registry.cierre_dependencias(['financiacion']),
            {'financiacion', 'ventas', 'productos', 'inventario'},
        )
        self.assertNotIn('cuentas_por_cobrar', registry.cierre_dependencias(['financiacion']))
