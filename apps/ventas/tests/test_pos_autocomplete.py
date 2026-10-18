"""
Regresion: el navegador no debe autocompletar el usuario/password guardados
del login en los campos de texto sueltos del POS (buscador de cliente, de
producto, etc).

Causa raiz: Chrome/Edge puede ignorar `autocomplete="off"` si detecta un campo
password oculto en el mismo formulario no reclamado. Los modales de
autorizacion vivian con `x-show`: no se veian, pero sus passwords ya existian
en el DOM y el navegador clasificaba los buscadores como username.
"""
from django.urls import reverse

from apps.ventas.tests.test_ventas_service import VentaServiceTestCase


class AutocompleteApagadoEnCamposDeTextoTests(VentaServiceTestCase):
    def test_buscador_de_cliente_y_de_producto_tienen_autocomplete_off(self):
        self.client.force_login(self.cajera)

        resp = self.client.get(reverse('pos:punto_venta'))

        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        for fragmento, nombre in (
            ('x-model="clienteBusqueda"', 'pos_customer_search'),
            ('x-model="busqueda"', 'pos_product_search'),
        ):
            inicio = html.index(fragmento)
            tag_inicio = html.rfind('<input', 0, inicio)
            tag_cierre = html.index('>', inicio)
            tag = html[tag_inicio:tag_cierre]
            self.assertIn(
                'autocomplete="off"', tag,
                f'falta autocomplete="off" en el input de {fragmento}',
            )
            self.assertIn(f'name="{nombre}"', tag)
            self.assertIn('data-form-type="other"', tag)
            self.assertIn('data-lpignore="true"', tag)

    def test_passwords_de_autorizacion_no_se_montan_con_modales_cerrados(self):
        self.client.force_login(self.cajera)

        resp = self.client.get(reverse('pos:punto_venta'))

        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('<template x-if="modalDescuentoAuthAbierto">', html)
        self.assertIn('<template x-if="modalCreditoAbierto">', html)
