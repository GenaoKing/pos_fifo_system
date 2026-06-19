"""
API-003: una venta replicada cuyo cajero no existe en cloud NO debe romper el
sync (IntegrityError, Venta.usuario es NOT NULL) ni el reporte ventas-por-cajero.

`_handler_venta_creada` cae al usuario_servicio de la sucursal cuando el
`usuario_username` del payload no resuelve; asi la venta SI se replica, y
build_ventas_por_cajero la procesa sin 500.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.api.services.reporting import build_ventas_por_cajero
from apps.api.views.sync import _handler_venta_creada
from apps.sucursales.models import Sucursal
from apps.ventas.models import Venta

User = get_user_model()


class VentaSinUsuarioSyncTests(TestCase):
    def setUp(self):
        self.svc = User.objects.create_user(
            username='svc_sd', email='svc_sd@test.local', password='x',
            rol='CAJERA', activo=True,
        )
        self.sucursal = Sucursal.objects.create(
            codigo='SD-001', nombre='Suc SD', activa=True, usuario_servicio=self.svc,
        )

    def _payload(self, numero='V-NOUSER', usuario_username='cajero_fantasma'):
        return {
            'numero_venta': numero,
            'usuario_username': usuario_username,
            'subtotal': '300.00',
            'total': '300.00',
            'estado': 'COMPLETADA',
            'condicion_pago': 'CONTADO',
            'fecha_venta': timezone.now().isoformat(),
        }

    def test_venta_con_cajero_inexistente_cae_a_usuario_servicio(self):
        # No debe lanzar IntegrityError: la venta se replica con el svc user.
        _handler_venta_creada(self.sucursal, self._payload())
        venta = Venta.objects.get(numero_venta='V-NOUSER')
        self.assertEqual(venta.usuario_id, self.svc.id)

    def test_ventas_por_cajero_no_revienta(self):
        _handler_venta_creada(self.sucursal, self._payload())
        hoy = timezone.localdate()
        data = build_ventas_por_cajero({'desde': str(hoy), 'hasta': str(hoy)})
        usernames = {c['username'] for c in data['cajeros']}
        self.assertIn('svc_sd', usernames)
