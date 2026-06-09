"""
Cutover del POS local: las vistas admin del POS quedan gateadas server-side por
permisos. Un cajero (sin el permiso) es bloqueado; el admin (acceso total) pasa
el gate. Esto cierra la vulnerabilidad de acceso por URL.
"""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.permisos.catalogo import sembrar_catalogo
from apps.permisos.models import Permiso

User = get_user_model()

# (url_name, permiso que la protege)
GATES_PAGINA = [
    ('auditoria:dashboard', 'auditoria.ver'),
    ('inventario:ajustes', 'inventario.ajustar'),
    ('pos:anulaciones', 'ventas.anular'),
    ('caja:historial', 'caja.administrar'),
]


class CutoverLocalGatesTests(TestCase):
    def setUp(self):
        sembrar_catalogo(Permiso)
        self.cajera = User.objects.create_user(
            'caja', 'c@e.com', 'x', rol='CAJERA', activo=True
        )
        self.admin = User.objects.create_user(
            'admin', 'a@e.com', 'x', rol='ADMIN', activo=True
        )

    def _client(self, user):
        c = Client()
        c.force_login(user)
        return c

    def test_cajera_bloqueada_en_cada_gate(self):
        """Sin el permiso, el cajero es redirigido (302) fuera de la vista admin."""
        client = self._client(self.cajera)
        for url_name, _permiso in GATES_PAGINA:
            with self.subTest(url=url_name):
                resp = client.get(reverse(url_name))
                self.assertEqual(
                    resp.status_code, 302,
                    f'{url_name} deberia redirigir al cajero (gate server-side)'
                )

    def test_admin_tiene_los_permisos_de_los_gates(self):
        """El admin (acceso total) tiene cada permiso → pasaria el gate."""
        for _url_name, permiso in GATES_PAGINA:
            with self.subTest(permiso=permiso):
                self.assertTrue(self.admin.tiene_permiso(permiso))
                self.assertFalse(self.cajera.tiene_permiso(permiso))
