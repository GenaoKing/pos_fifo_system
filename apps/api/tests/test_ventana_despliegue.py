"""
Seguridad de la VENTANA de despliegue: cloud NUEVO con sucursales todavia VIEJAS.

El cloud se despliega antes que el paquete de cada sucursal (una visita por
cliente lleva dias). Durante ese periodo el cloud recibe payloads del formato
anterior, sin el bloque `cliente`.

Lo que se garantiza aqui:

1. Las ventas y cuentas siguen replicando: desplegar el cloud NO corta el sync.
2. Una CxC nacida en esa ventana queda a nombre del generico CLIENTE CONTADO
   (mejor que perderla), pero **el reenvio posterior corrige el titular**.

El punto 2 es el que importa: sin la correccion, el handler saltaba la cuenta
por existir y el titular equivocado quedaba para siempre.
"""
from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model
from apps.api.views.sync import _handler_cxc_creada, _handler_venta_creada
from apps.clientes.models import Cliente
from apps.cuentas_por_cobrar.models import CuentaPorCobrar
from apps.sucursales.models import Sucursal
from apps.ventas.models import Venta

User = get_user_model()


class VentanaDeDespliegueTests(TestCase):
    """Payloads VIEJOS (sin bloque `cliente`) contra el cloud NUEVO."""

    def setUp(self):
        u = User.objects.create_user('svc_v', 'svc_v@t.local', 'x', rol='CAJERA')
        self.suc = Sucursal.objects.create(codigo='SD-V', nombre='S', activa=True,
                                           usuario_servicio=u)
        self.usuario = u

    def _payload_venta_viejo(self, numero, condicion='CREDITO'):
        # Formato de junio: sin bloque `cliente`, cliente sin cedula => None
        return {'numero_venta': numero, 'sucursal_codigo': self.suc.codigo,
                'fecha_venta': '2026-08-19T10:00:00-04:00',
                'usuario_username': self.usuario.username,
                'cliente_cedula_rnc': None, 'cliente_nombre': 'Ferreteria X',
                'subtotal': '5000.00', 'descuento_total': '0.00', 'total': '5000.00',
                'estado': 'COMPLETADA', 'condicion_pago': condicion, 'notas': '',
                'detalles': [], 'pagos': []}

    def _payload_cxc_viejo(self, numero):
        return {'cuenta_id_local': 1, 'numero_venta': numero,
                'sucursal_codigo': self.suc.codigo,
                'cliente_cedula_rnc': None, 'cliente_nombre': 'Ferreteria X',
                'metodo_plazo': 'C30', 'modalidad': 'VENCIMIENTO_UNICO',
                'metodo_plazo_tipo': 'VENCIMIENTO_UNICO',
                'metodo_plazo_frecuencia': 'MENSUAL', 'metodo_plazo_cantidad_cuotas': 1,
                'metodo_plazo_dias_vencimiento': 30, 'total': '5000.00',
                'monto_inicial': '0.00', 'saldo_original': '5000.00',
                'interes_porcentaje': '0.00', 'monto_interes': '0.00',
                'saldo': '5000.00', 'estado': 'ABIERTA',
                'fecha_emision': '2026-08-19', 'fecha_limite': '2026-09-18',
                'override_autorizado_por_username': None, 'cuotas': []}

    def test_venta_vieja_sigue_replicando(self):
        _handler_venta_creada(self.suc, self._payload_venta_viejo('V-1', 'CONTADO'))
        self.assertTrue(Venta.objects.filter(numero_venta='V-1').exists())

    def test_cxc_vieja_a_quien_queda_asignada(self):
        _handler_venta_creada(self.suc, self._payload_venta_viejo('V-2'))
        _handler_cxc_creada(self.suc, self._payload_cxc_viejo('V-2'))
        cta = CuentaPorCobrar.objects.get(venta__numero_venta='V-2')
        print(f'\n>>> CxC de payload VIEJO quedo a nombre de: {cta.cliente.nombre!r}')
        print(f'>>> es el generico CONTADO: {cta.cliente.tipo == "CONTADO"}')

    def test_reenvio_posterior_con_payload_nuevo_corrige_el_titular(self):
        """Tras actualizar la sucursal, el re-envio deberia arreglar el titular."""
        _handler_venta_creada(self.suc, self._payload_venta_viejo('V-3'))
        _handler_cxc_creada(self.suc, self._payload_cxc_viejo('V-3'))

        nuevo = dict(self._payload_cxc_viejo('V-3'))
        nuevo['cliente'] = {'id_local': 55, 'tipo': 'CORPORATIVO',
                            'nombre': 'Ferreteria X', 'cedula_rnc': None,
                            'telefono': '', 'direccion': '',
                            'limite_credito': '0.00', 'plazo_credito_dias': 30}
        _handler_cxc_creada(self.suc, nuevo)

        cta = CuentaPorCobrar.objects.get(venta__numero_venta='V-3')
        print(f'\n>>> Tras re-enviar con payload NUEVO: {cta.cliente.nombre!r} '
              f'(tipo={cta.cliente.tipo})')
        self.assertNotEqual(cta.cliente.tipo, 'CONTADO',
                            'El titular quedo mal y el re-envio NO lo corrigio')
