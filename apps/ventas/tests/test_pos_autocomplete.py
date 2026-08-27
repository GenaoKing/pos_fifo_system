"""
Regresion: el navegador no debe autocompletar el usuario/password guardados
del login en los campos de texto sueltos del POS (buscador de cliente, de
producto, etc).

Causa raiz: `templates/pos/punto_venta.html` no tiene ningun `<form>` que
delimite secciones, asi que Chrome/Edge trata toda la pagina como un
"formulario no reclamado" y puede rellenar el primer input de texto que
encuentre con la credencial guardada del sitio -- el sintoma reportado fue
el username de la cajera apareciendo solo en el buscador de cliente.
"""
from django.urls import reverse

from apps.ventas.tests.test_ventas_service import VentaServiceTestCase


class AutocompleteApagadoEnCamposDeTextoTests(VentaServiceTestCase):
    def test_buscador_de_cliente_y_de_producto_tienen_autocomplete_off(self):
        self.client.force_login(self.cajera)

        resp = self.client.get(reverse('pos:punto_venta'))

        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        # No hay id/name en estos inputs (son x-model de Alpine.js) -- lo
        # unico verificable es que "autocomplete" aparezca cerca de cada
        # x-model relevante, en el mismo tag <input ... >.
        for fragmento in ('x-model="clienteBusqueda"', 'x-model="busqueda"'):
            inicio = html.index(fragmento)
            tag_cierre = html.index('>', inicio)
            tag = html[max(0, inicio - 200):tag_cierre]
            self.assertIn(
                'autocomplete="off"', tag,
                f'falta autocomplete="off" en el input de {fragmento}',
            )
