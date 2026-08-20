"""
Tests del resolutor de clientes del cloud (Fase 1, BUG-C).

Contexto medido en produccion el 2026-08-19: los handlers resolvian al cliente
SOLO por `cedula_rnc`, que es opcional y en la practica viene vacio. Resultado:
404 de 405 ventas replicadas sin cliente, y las 16 ventas a credito de Royal
Plast (RD$240,435) sin poder crear su cuenta por cobrar -- el handler lanzaba
ValueError, agotaba reintentos y el evento moria.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.api.views.sync import _handler_cxc_creada, _handler_venta_creada
from apps.clientes.models import Cliente
from apps.cuentas_por_cobrar.models import CuentaPorCobrar
from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal
from apps.ventas.models import Venta

User = get_user_model()


class ClienteUpsertTestsBase(TestCase):
    def setUp(self):
        self.servicio = User.objects.create_user(
            'servicio_sd', 'servicio_sd@test.local', 'x', rol='CAJERA'
        )
        self.sucursal = Sucursal.objects.create(
            codigo='SD-001', nombre='Sucursal SD', activa=True,
            usuario_servicio=self.servicio,
        )
        self.categoria = Categoria.objects.create(nombre='Plasticos')
        self.producto = Producto.objects.create(
            sku='SKU-UP-1', nombre='Vaso', categoria=self.categoria,
            precio_venta=Decimal('25.00'), stock_minimo=5,
        )

    def _payload_venta(self, numero='V-20260819-0001', cliente=None, condicion='CONTADO'):
        payload = {
            'numero_venta': numero,
            'sucursal_codigo': self.sucursal.codigo,
            'fecha_venta': '2026-08-19T10:00:00-04:00',
            'usuario_username': self.servicio.username,
            'subtotal': '100.00',
            'descuento_total': '0.00',
            'total': '100.00',
            'estado': 'COMPLETADA',
            'condicion_pago': condicion,
            'notas': '',
            'detalles': [],
            'pagos': [],
        }
        if cliente is not None:
            payload['cliente'] = cliente
            payload['cliente_cedula_rnc'] = cliente.get('cedula_rnc')
            payload['cliente_nombre'] = cliente.get('nombre')
        return payload

    @staticmethod
    def _cliente_sin_cedula(id_local=77, nombre='German tienda 20 y 10'):
        return {
            'id_local': id_local,
            'tipo': 'CORPORATIVO',
            'nombre': nombre,
            'cedula_rnc': None,
            'telefono': '809-555-0100',
            'direccion': 'Calle Falsa 123',
            'limite_credito': '50000.00',
            'plazo_credito_dias': 30,
        }


class ResolucionDeClienteTests(ClienteUpsertTestsBase):
    def test_cliente_sin_cedula_se_crea_y_la_venta_queda_enlazada(self):
        """El caso exacto de Royal Plast: cliente de mostrador, sin cedula."""
        payload = self._payload_venta(cliente=self._cliente_sin_cedula())

        _handler_venta_creada(self.sucursal, payload)

        venta = Venta.objects.get(numero_venta='V-20260819-0001')
        self.assertIsNotNone(venta.cliente_id, 'La venta replico sin cliente')
        self.assertEqual(venta.cliente.nombre, 'German tienda 20 y 10')
        self.assertEqual(venta.cliente.origen_sucursal, self.sucursal)
        self.assertEqual(venta.cliente.origen_id_local, 77)
        self.assertEqual(venta.cliente.tipo, 'CORPORATIVO')

    def test_segundo_evento_del_mismo_cliente_no_lo_duplica(self):
        datos = self._cliente_sin_cedula()
        _handler_venta_creada(self.sucursal, self._payload_venta('V-1', cliente=datos))
        _handler_venta_creada(self.sucursal, self._payload_venta('V-2', cliente=datos))

        self.assertEqual(Cliente.objects.filter(origen_id_local=77).count(), 1)
        self.assertEqual(
            Venta.objects.get(numero_venta='V-1').cliente_id,
            Venta.objects.get(numero_venta='V-2').cliente_id,
        )

    def test_cliente_con_cedula_existente_se_reutiliza(self):
        """La cedula sigue mandando cuando existe: es la identidad real."""
        existente = Cliente.objects.create(
            tipo='PERSONAL', nombre='Santiago Genao', cedula_rnc='40208777264',
        )
        datos = self._cliente_sin_cedula(id_local=99, nombre='Santiago G.')
        datos['cedula_rnc'] = '40208777264'

        _handler_venta_creada(self.sucursal, self._payload_venta(cliente=datos))

        venta = Venta.objects.get(numero_venta='V-20260819-0001')
        self.assertEqual(venta.cliente_id, existente.pk)
        self.assertEqual(Cliente.objects.filter(cedula_rnc='40208777264').count(), 1)

    def test_cedula_agregada_despues_se_sube_al_cloud(self):
        """
        Sin este backfill, el pull de maestros devolveria la cedula vacia a la
        sucursal y borraria lo que el cajero tecleo.
        """
        datos = self._cliente_sin_cedula()
        _handler_venta_creada(self.sucursal, self._payload_venta('V-1', cliente=datos))

        datos_con_cedula = dict(datos, cedula_rnc='00112345678')
        _handler_venta_creada(
            self.sucursal, self._payload_venta('V-2', cliente=datos_con_cedula)
        )

        clientes = Cliente.objects.filter(origen_id_local=77)
        self.assertEqual(clientes.count(), 1)
        self.assertEqual(clientes.first().cedula_rnc, '00112345678')

    def test_payload_viejo_sin_bloque_cliente_no_revienta(self):
        """Los eventos ya encolados con el formato anterior deben seguir aplicando."""
        payload = self._payload_venta()
        payload.pop('cliente', None)

        _handler_venta_creada(self.sucursal, payload)

        venta = Venta.objects.get(numero_venta='V-20260819-0001')
        self.assertIsNone(venta.cliente_id)
        self.assertEqual(Cliente.objects.count(), 0)


class CuentaPorCobrarTests(ClienteUpsertTestsBase):
    def _payload_cxc(self, numero_venta, cliente):
        return {
            'cuenta_id_local': 5,
            'numero_venta': numero_venta,
            'sucursal_codigo': self.sucursal.codigo,
            'cliente': cliente,
            'cliente_cedula_rnc': cliente.get('cedula_rnc'),
            'cliente_nombre': cliente.get('nombre'),
            'metodo_plazo': 'Credito 30 dias',
            'modalidad': 'VENCIMIENTO_UNICO',
            'metodo_plazo_tipo': 'VENCIMIENTO_UNICO',
            'metodo_plazo_frecuencia': 'MENSUAL',
            'metodo_plazo_cantidad_cuotas': 1,
            'metodo_plazo_dias_vencimiento': 30,
            'total': '7800.00',
            'monto_inicial': '0.00',
            'saldo_original': '7800.00',
            'interes_porcentaje': '0.00',
            'monto_interes': '0.00',
            'saldo': '7800.00',
            'estado': 'ABIERTA',
            'fecha_emision': '2026-08-19',
            'fecha_limite': '2026-09-18',
            'override_autorizado_por_username': None,
            'cuotas': [
                {
                    'numero': 1,
                    'monto': '7800.00',
                    'saldo': '7800.00',
                    'fecha_vencimiento': '2026-09-18',
                    'estado': 'PENDIENTE',
                },
            ],
        }

    def test_cxc_de_cliente_sin_cedula_ya_no_se_rechaza(self):
        """
        Reproduce BUG-C completo. Antes este mismo flujo lanzaba
        ValueError('Cliente de CxC ... no existe en cloud') y la deuda nunca
        aparecia en el portal.
        """
        datos = self._cliente_sin_cedula()
        numero = 'V-20260622-0001'
        _handler_venta_creada(
            self.sucursal,
            self._payload_venta(numero, cliente=datos, condicion='CREDITO'),
        )

        _handler_cxc_creada(self.sucursal, self._payload_cxc(numero, datos))

        venta = Venta.objects.get(numero_venta=numero)
        cuenta = CuentaPorCobrar.objects.get(venta=venta)
        self.assertEqual(cuenta.cliente.nombre, 'German tienda 20 y 10')
        self.assertEqual(cuenta.total, Decimal('7800.00'))
        self.assertEqual(venta.cliente_id, cuenta.cliente_id)

    def test_cxc_repetida_no_duplica_la_cuenta(self):
        datos = self._cliente_sin_cedula()
        numero = 'V-20260622-0002'
        _handler_venta_creada(
            self.sucursal,
            self._payload_venta(numero, cliente=datos, condicion='CREDITO'),
        )
        payload = self._payload_cxc(numero, datos)

        _handler_cxc_creada(self.sucursal, payload)
        _handler_cxc_creada(self.sucursal, payload)

        venta = Venta.objects.get(numero_venta=numero)
        self.assertEqual(CuentaPorCobrar.objects.filter(venta=venta).count(), 1)
        self.assertEqual(Cliente.objects.filter(origen_id_local=77).count(), 1)
