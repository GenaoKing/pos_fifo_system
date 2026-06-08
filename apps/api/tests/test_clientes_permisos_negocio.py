"""
Test de aceptacion del RBAC multitenant sobre el endpoint de clientes del portal.

Criterio (ejemplo del usuario): el mismo rol "Cajero" configurado distinto por
negocio produce resultados distintos en el MISMO endpoint:
  - Cajero de Royal Plast con 'clientes.crear'  -> POST 201
  - Cajero de SK Performance sin 'clientes.crear' -> POST 403

El enforcement es server-side (default-deny); demuestra que cerrar la UI ya no
es necesario para proteger el endpoint.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.permisos import testing

User = get_user_model()


def _cajera(username):
    return User.objects.create_user(
        username=username, email=f'{username}@example.com', password='x', rol='CAJERA'
    )


class ClientesPermisoPorNegocioTests(TestCase):
    url = '/api/v1/maestros/clientes/'

    def setUp(self):
        self.negocio_rp = testing.crear_negocio('Royal Plast')
        self.negocio_sk = testing.crear_negocio('SK Performance')

        # Mismo nombre de rol, permisos distintos por negocio.
        self.cajero_rp = testing.crear_rol(
            self.negocio_rp, 'Cajero', ['clientes.ver', 'clientes.crear']
        )
        self.cajero_sk = testing.crear_rol(
            self.negocio_sk, 'Cajero', ['clientes.ver']
        )

        self.user_rp = _cajera('caja_rp')
        self.user_sk = _cajera('caja_sk')
        testing.asignar(self.user_rp, self.cajero_rp)
        testing.asignar(self.user_sk, self.cajero_sk)

    def _api(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def _payload(self, nombre, cedula):
        return {'tipo': 'PERSONAL', 'nombre': nombre, 'cedula_rnc': cedula}

    def test_cajero_royal_plast_puede_crear_cliente(self):
        r = self._api(self.user_rp).post(
            self.url, self._payload('Cliente RP', '40200000001'), format='json'
        )
        self.assertEqual(r.status_code, 201, r.data)

    def test_cajero_sk_no_puede_crear_cliente(self):
        r = self._api(self.user_sk).post(
            self.url, self._payload('Cliente SK', '40200000002'), format='json'
        )
        self.assertEqual(r.status_code, 403, r.data)

    def test_ambos_cajeros_pueden_listar(self):
        self.assertEqual(self._api(self.user_rp).get(self.url).status_code, 200)
        self.assertEqual(self._api(self.user_sk).get(self.url).status_code, 200)

    def test_rol_sin_permiso_ver_no_puede_listar(self):
        user = _cajera('caja_solo_ventas')
        rol = testing.crear_rol(self.negocio_rp, 'Solo Ventas', ['ventas.crear'])
        testing.asignar(user, rol)
        self.assertEqual(self._api(user).get(self.url).status_code, 403)
