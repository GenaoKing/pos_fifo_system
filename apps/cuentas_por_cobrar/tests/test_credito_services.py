from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.clientes.models import Cliente
from apps.cuentas_por_cobrar.models import CuentaPorCobrar, CuotaCxC, MetodoPlazoCredito
from apps.cuentas_por_cobrar.services import (
    registrar_pago_cxc_service,
    reprogramar_cxc_por_plazo_cliente,
)
from apps.inventario.models import Compra, DetalleCompra
from apps.productos.models import Categoria, Producto
from apps.ventas.models import Pago, Venta
from apps.ventas.services import LimiteCreditoExcedidoError, procesar_venta_service


class CreditoServicesTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='admin_credito',
            email='admin_credito@test.local',
            password='pass',
            rol='ADMIN',
            activo=True,
        )
        self.cajera = User.objects.create_user(
            username='cajera_credito',
            email='cajera_credito@test.local',
            password='pass',
            rol='CAJERA',
            activo=True,
        )
        self.categoria = Categoria.objects.create(nombre='Credito Test')
        self.producto = Producto.objects.create(
            sku='CXC-PROD-001',
            codigo_barras='CXC-PROD-001',
            nombre='Producto credito',
            descripcion='',
            categoria=self.categoria,
            precio_venta=Decimal('100.00'),
            stock_minimo=1,
            activo=True,
            estado='nuevo',
            marca='',
            atributos={},
        )
        self.compra = Compra.objects.create(
            usuario=self.admin,
            proveedor='Proveedor CxC',
            numero_factura='FAC-CXC-001',
            total=Decimal('1000.00'),
        )
        self.detalle_compra = DetalleCompra.objects.create(
            compra=self.compra,
            producto=self.producto,
            cantidad=10,
            costo_unitario=Decimal('50.00'),
            subtotal=Decimal('500.00'),
        )
        self.cliente = Cliente.objects.create(
            tipo='CORPORATIVO',
            nombre='Cliente Credito',
            cedula_rnc='131999001',
            limite_credito=Decimal('1000.00'),
            plazo_credito_dias=30,
            activo=True,
        )
        self.metodo_unico = MetodoPlazoCredito.objects.create(
            nombre='30 dias test',
            tipo=MetodoPlazoCredito.TIPO_VENCIMIENTO_UNICO,
            dias_vencimiento=30,
            cantidad_cuotas=1,
            frecuencia=MetodoPlazoCredito.FRECUENCIA_MENSUAL,
            inicial_minima_porcentaje=Decimal('0.00'),
            activo=True,
        )
        self.metodo_cuotas = MetodoPlazoCredito.objects.create(
            nombre='3 cuotas test',
            tipo=MetodoPlazoCredito.TIPO_CUOTAS,
            dias_vencimiento=15,
            cantidad_cuotas=3,
            frecuencia=MetodoPlazoCredito.FRECUENCIA_MENSUAL,
            inicial_minima_porcentaje=Decimal('0.00'),
            activo=True,
        )

    def _payload_credito(self, *, total='200.00', cantidad=2, credito=None):
        credito_base = {
            'modalidad': 'VENCIMIENTO_UNICO',
            'metodo_plazo_id': self.metodo_unico.id,
            'fecha_primer_vencimiento': '2026-06-30',
            'monto_inicial': '50.00',
            'metodo_inicial': 'efectivo',
        }
        credito_base.update(credito or {})
        return {
            'carrito': [
                {
                    'id': self.producto.id,
                    'cantidad': cantidad,
                    'precio_venta': '100.00',
                    'descuento': '0.00',
                }
            ],
            'metodo_pago': 'credito',
            'cliente_id': self.cliente.id,
            'total': total,
            'tipo_ecf': '31',
            'credito': credito_base,
        }

    def test_venta_credito_vencimiento_unico_crea_cuenta_cuota_y_pago_credito(self):
        self.cliente.plazo_credito_dias = 90
        self.cliente.save(update_fields=['plazo_credito_dias'])

        with patch('apps.cuentas_por_cobrar.services.timezone.localdate', return_value=date(2026, 6, 1)):
            venta = procesar_venta_service(
                usuario=self.cajera,
                datos=self._payload_credito(),
            )

        cuenta = venta.cuenta_por_cobrar
        cuota = cuenta.cuotas.get()

        self.assertEqual(venta.condicion_pago, 'CREDITO')
        self.assertEqual(cuenta.total, Decimal('200.00'))
        self.assertEqual(cuenta.saldo, Decimal('150.00'))
        self.assertEqual(cuenta.fecha_emision, date(2026, 6, 1))
        self.assertEqual(cuenta.fecha_limite, date(2026, 8, 30))
        self.assertEqual(cuota.monto, Decimal('150.00'))
        self.assertEqual(cuota.saldo, Decimal('150.00'))
        self.assertEqual(cuota.fecha_vencimiento, date(2026, 8, 30))
        self.assertEqual(
            list(venta.pagos.order_by('metodo').values_list('metodo', 'monto')),
            [('CREDITO', Decimal('150.00')), ('EFECTIVO', Decimal('50.00'))],
        )
        self.detalle_compra.lote.refresh_from_db()
        self.assertEqual(self.detalle_compra.lote.cantidad_actual, 8)

    def test_venta_credito_en_cuotas_divide_saldo_y_respeta_inicial(self):
        venta = procesar_venta_service(
            usuario=self.cajera,
            datos=self._payload_credito(
                total='100.00',
                cantidad=1,
                credito={
                    'modalidad': 'CUOTAS',
                    'metodo_plazo_id': self.metodo_cuotas.id,
                    'monto_inicial': '10.00',
                    'cantidad_cuotas': 3,
                    'fecha_primer_vencimiento': '2026-06-15',
                },
            ),
        )

        cuotas = list(venta.cuenta_por_cobrar.cuotas.order_by('numero'))

        self.assertEqual(venta.cuenta_por_cobrar.saldo, Decimal('90.00'))
        self.assertEqual([c.monto for c in cuotas], [Decimal('30.00')] * 3)
        self.assertEqual(
            [c.fecha_vencimiento for c in cuotas],
            [date(2026, 6, 15), date(2026, 7, 15), date(2026, 8, 14)],
        )

    def test_venta_credito_en_cuotas_sin_metodo_plazo_usa_fallback_por_tipo(self):
        # El POS ya no envia metodo_plazo_id: el backend resuelve el primer
        # metodo activo de tipo CUOTAS y la venta se configura con los datos
        # del payload (cuotas, frecuencia, fecha).
        venta = procesar_venta_service(
            usuario=self.cajera,
            datos=self._payload_credito(
                total='100.00',
                cantidad=1,
                credito={
                    'modalidad': 'CUOTAS',
                    'metodo_plazo_id': None,
                    'monto_inicial': '10.00',
                    'cantidad_cuotas': 3,
                    'fecha_primer_vencimiento': '2026-06-15',
                },
            ),
        )

        cuenta = venta.cuenta_por_cobrar
        # El fallback toma el primer metodo CUOTAS activo (puede ser el
        # seedeado por migracion); lo relevante es el tipo y que la cuenta
        # siga el payload (cuotas, inicial).
        self.assertEqual(cuenta.metodo_plazo.tipo, MetodoPlazoCredito.TIPO_CUOTAS)
        self.assertEqual(cuenta.cuotas.count(), 3)
        self.assertEqual(cuenta.saldo, Decimal('90.00'))

    def test_limite_credito_bloquea_sin_override_y_hace_rollback(self):
        self.cliente.limite_credito = Decimal('100.00')
        self.cliente.save(update_fields=['limite_credito'])

        with self.assertRaises(LimiteCreditoExcedidoError):
            procesar_venta_service(
                usuario=self.cajera,
                datos=self._payload_credito(
                    credito={'monto_inicial': '0.00'},
                ),
            )

        self.assertEqual(Venta.objects.count(), 0)
        self.assertEqual(CuentaPorCobrar.objects.count(), 0)
        self.assertEqual(Pago.objects.count(), 0)
        self.detalle_compra.lote.refresh_from_db()
        self.assertEqual(self.detalle_compra.lote.cantidad_actual, 10)

    def test_limite_credito_permite_override_admin_y_deja_auditoria_en_cuenta(self):
        self.cliente.limite_credito = Decimal('100.00')
        self.cliente.save(update_fields=['limite_credito'])

        venta = procesar_venta_service(
            usuario=self.cajera,
            datos=self._payload_credito(
                credito={
                    'monto_inicial': '0.00',
                    'admin_override_id': self.admin.id,
                    'motivo_override': 'Cliente autorizado por gerencia',
                },
            ),
        )

        cuenta = venta.cuenta_por_cobrar
        self.assertEqual(cuenta.override_autorizado_por, self.admin)
        self.assertEqual(cuenta.motivo_override, 'Cliente autorizado por gerencia')
        self.assertEqual(cuenta.saldo, Decimal('200.00'))

    def test_abono_aplica_a_cuotas_mas_antiguas_y_actualiza_saldos(self):
        venta = procesar_venta_service(
            usuario=self.cajera,
            datos=self._payload_credito(
                total='100.00',
                cantidad=1,
                credito={
                    'modalidad': 'CUOTAS',
                    'metodo_plazo_id': self.metodo_cuotas.id,
                    'monto_inicial': '10.00',
                    'cantidad_cuotas': 3,
                    'fecha_primer_vencimiento': '2026-06-15',
                },
            ),
        )
        cuenta = venta.cuenta_por_cobrar

        pago = registrar_pago_cxc_service(
            cuenta_id=cuenta.id,
            usuario=self.cajera,
            metodo='EFECTIVO',
            monto=Decimal('40.00'),
            referencia='ABONO-001',
        )

        cuenta.refresh_from_db()
        cuotas = list(CuotaCxC.objects.filter(cuenta=cuenta).order_by('numero'))

        self.assertEqual(pago.monto, Decimal('40.00'))
        self.assertEqual(cuenta.saldo, Decimal('50.00'))
        self.assertEqual(cuenta.estado, CuentaPorCobrar.ESTADO_PARCIAL)
        self.assertEqual(cuotas[0].estado, CuotaCxC.ESTADO_PAGADA)
        self.assertEqual(cuotas[0].saldo, Decimal('0.00'))
        self.assertEqual(cuotas[1].estado, CuotaCxC.ESTADO_PARCIAL)
        self.assertEqual(cuotas[1].saldo, Decimal('20.00'))
        self.assertEqual(cuotas[2].estado, CuotaCxC.ESTADO_PENDIENTE)

    def test_reprogramar_plazo_cliente_solo_toca_vencimiento_unico_abiertas(self):
        with patch('apps.cuentas_por_cobrar.services.timezone.localdate', return_value=date(2026, 6, 1)):
            venta_unica = procesar_venta_service(
                usuario=self.cajera,
                datos=self._payload_credito(),
            )
            venta_cuotas = procesar_venta_service(
                usuario=self.cajera,
                datos=self._payload_credito(
                    total='100.00',
                    cantidad=1,
                    credito={
                        'modalidad': 'CUOTAS',
                        'metodo_plazo_id': self.metodo_cuotas.id,
                        'monto_inicial': '10.00',
                        'cantidad_cuotas': 3,
                        'fecha_primer_vencimiento': '2026-06-15',
                    },
                ),
            )

        cuenta_unica = venta_unica.cuenta_por_cobrar
        cuenta_cuotas = venta_cuotas.cuenta_por_cobrar
        fechas_cuotas_antes = list(
            cuenta_cuotas.cuotas.order_by('numero').values_list('fecha_vencimiento', flat=True)
        )

        self.cliente.plazo_credito_dias = 90
        self.cliente.save(update_fields=['plazo_credito_dias'])
        with patch('apps.cuentas_por_cobrar.models.timezone.localdate', return_value=date(2026, 6, 10)):
            resultado = reprogramar_cxc_por_plazo_cliente(
                self.cliente,
                usuario=self.admin,
                origen='test',
                plazo_anterior=30,
            )

        cuenta_unica.refresh_from_db()
        cuenta_cuotas.refresh_from_db()
        self.assertEqual(resultado['cuentas_afectadas'], 1)
        self.assertEqual(cuenta_unica.fecha_limite, date(2026, 8, 30))
        self.assertEqual(cuenta_unica.cuotas.get().fecha_vencimiento, date(2026, 8, 30))
        self.assertEqual(
            list(cuenta_cuotas.cuotas.order_by('numero').values_list('fecha_vencimiento', flat=True)),
            fechas_cuotas_antes,
        )

        self.cliente.plazo_credito_dias = 1
        self.cliente.save(update_fields=['plazo_credito_dias'])
        with patch('apps.cuentas_por_cobrar.models.timezone.localdate', return_value=date(2026, 6, 10)):
            reprogramar_cxc_por_plazo_cliente(
                self.cliente,
                usuario=self.admin,
                origen='test',
                plazo_anterior=90,
            )

        cuenta_unica.refresh_from_db()
        self.assertEqual(cuenta_unica.fecha_limite, date(2026, 6, 2))
        self.assertEqual(cuenta_unica.estado, CuentaPorCobrar.ESTADO_VENCIDA)
