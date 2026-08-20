from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.clientes.models import Cliente
from apps.cuentas_por_cobrar.models import CuentaPorCobrar, MetodoPlazoCredito
from apps.cuentas_por_cobrar.services import registrar_pago_cxc_service
from apps.inventario.models import Compra, DetalleCompra
from apps.productos.models import Categoria, Producto
from apps.sync.serializers import serializar_cxc
from apps.permisos.testing import habilitar_cajero
from apps.ventas.models import Pago
from apps.ventas.services import (
    LimiteCreditoExcedidoError,
    MetodoPlazoCreditoInvalidoError,
    procesar_venta_service,
)


class InteresFinanciamientoTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='admin_interes',
            email='admin_interes@test.local',
            password='pass',
            rol='ADMIN',
            activo=True,
        )
        self.cajera = User.objects.create_user(
            username='cajera_interes',
            email='cajera_interes@test.local',
            password='pass',
            rol='CAJERA',
            activo=True,
        )
        # La venta exige `ventas.crear` server-side (RBAC del catalogo).
        habilitar_cajero(self.cajera)
        self.categoria = Categoria.objects.create(nombre='Interes Test')
        self.producto = Producto.objects.create(
            sku='INT-PROD-001',
            codigo_barras='INT-PROD-001',
            nombre='Producto interes',
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
            proveedor='Proveedor Interes',
            numero_factura='FAC-INT-001',
            total=Decimal('1000.00'),
        )
        DetalleCompra.objects.create(
            compra=self.compra,
            producto=self.producto,
            cantidad=10,
            costo_unitario=Decimal('50.00'),
            subtotal=Decimal('500.00'),
        )
        self.cliente = Cliente.objects.create(
            tipo='CORPORATIVO',
            nombre='Cliente Interes',
            cedula_rnc='131999002',
            limite_credito=Decimal('1000.00'),
            activo=True,
        )
        self.metodo_unico = MetodoPlazoCredito.objects.create(
            nombre='30 dias interes test',
            tipo=MetodoPlazoCredito.TIPO_VENCIMIENTO_UNICO,
            dias_vencimiento=30,
            cantidad_cuotas=1,
            frecuencia=MetodoPlazoCredito.FRECUENCIA_MENSUAL,
            inicial_minima_porcentaje=Decimal('0.00'),
            activo=True,
        )
        self.metodo_cuotas = MetodoPlazoCredito.objects.create(
            nombre='3 cuotas interes test',
            tipo=MetodoPlazoCredito.TIPO_CUOTAS,
            dias_vencimiento=15,
            cantidad_cuotas=3,
            frecuencia=MetodoPlazoCredito.FRECUENCIA_MENSUAL,
            inicial_minima_porcentaje=Decimal('0.00'),
            interes_porcentaje=Decimal('5.00'),
            activo=True,
        )

    def _vender(self, *, total='100.00', cantidad=1, credito=None):
        credito_base = {
            'metodo_plazo_id': self.metodo_cuotas.id,
            'fecha_primer_vencimiento': '2026-07-15',
            'monto_inicial': '10.00',
            'metodo_inicial': 'efectivo',
            'cantidad_cuotas': 3,
        }
        credito_base.update(credito or {})
        return procesar_venta_service(
            usuario=self.cajera,
            datos={
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
            },
        )

    def test_interes_cero_equivale_a_comportamiento_anterior(self):
        venta = self._vender(credito={'interes_porcentaje': '0'})
        cuenta = venta.cuenta_por_cobrar

        self.assertEqual(cuenta.saldo_original, Decimal('90.00'))
        self.assertEqual(cuenta.monto_interes, Decimal('0.00'))
        self.assertEqual(cuenta.interes_porcentaje, Decimal('0.00'))
        self.assertEqual(cuenta.saldo, Decimal('90.00'))
        self.assertEqual(cuenta.monto_financiado, Decimal('90.00'))

    def test_interes_explicito_distribuye_en_cuotas_con_suma_exacta(self):
        venta = self._vender(credito={'interes_porcentaje': '10'})
        cuenta = venta.cuenta_por_cobrar
        cuotas = list(cuenta.cuotas.order_by('numero'))

        self.assertEqual(cuenta.saldo_original, Decimal('90.00'))
        self.assertEqual(cuenta.monto_interes, Decimal('9.00'))
        self.assertEqual(cuenta.saldo, Decimal('99.00'))
        self.assertEqual([c.monto for c in cuotas], [Decimal('33.00')] * 3)
        self.assertEqual(sum(c.monto for c in cuotas), cuenta.saldo)

    def test_redondeo_lo_absorbe_la_ultima_cuota(self):
        # capital 100, interes 10% -> financiado 110; 110/3 = 36.67 base
        venta = self._vender(
            credito={'monto_inicial': '0.00', 'interes_porcentaje': '10'},
        )
        cuenta = venta.cuenta_por_cobrar
        cuotas = list(cuenta.cuotas.order_by('numero'))

        self.assertEqual(cuenta.saldo, Decimal('110.00'))
        self.assertEqual([c.monto for c in cuotas], [Decimal('36.67'), Decimal('36.67'), Decimal('36.66')])
        self.assertEqual(sum(c.monto for c in cuotas), Decimal('110.00'))

    def test_interes_del_metodo_aplica_como_fallback(self):
        # metodo_cuotas tiene interes_porcentaje=5.00 y el payload no manda interes
        venta = self._vender()
        cuenta = venta.cuenta_por_cobrar

        self.assertEqual(cuenta.interes_porcentaje, Decimal('5.00'))
        self.assertEqual(cuenta.monto_interes, Decimal('4.50'))
        self.assertEqual(cuenta.saldo, Decimal('94.50'))

    def test_vencimiento_unico_con_interes_crea_una_cuota_total(self):
        venta = self._vender(
            credito={
                'metodo_plazo_id': self.metodo_unico.id,
                'interes_porcentaje': '10',
            },
        )
        cuenta = venta.cuenta_por_cobrar
        cuota = cuenta.cuotas.get()

        self.assertEqual(cuenta.saldo, Decimal('99.00'))
        self.assertEqual(cuota.monto, Decimal('99.00'))

    def test_interes_no_modifica_venta_ni_pago_credito(self):
        venta = self._vender(credito={'interes_porcentaje': '10'})

        self.assertEqual(venta.total, Decimal('100.00'))
        pago_credito = Pago.objects.get(venta=venta, metodo='CREDITO')
        self.assertEqual(pago_credito.monto, Decimal('90.00'))

    def test_limite_se_valida_contra_monto_financiado(self):
        # capital 90 cabe en el limite, pero financiado 99 no
        self.cliente.limite_credito = Decimal('95.00')
        self.cliente.save(update_fields=['limite_credito'])

        with self.assertRaises(LimiteCreditoExcedidoError):
            self._vender(credito={'interes_porcentaje': '10'})

        self.assertEqual(CuentaPorCobrar.objects.count(), 0)

    def test_interes_invalido_rechaza_venta(self):
        with self.assertRaises(MetodoPlazoCreditoInvalidoError):
            self._vender(credito={'interes_porcentaje': '150'})

    def test_estados_parcial_y_pagada_con_interes(self):
        venta = self._vender(credito={'interes_porcentaje': '10'})
        cuenta = venta.cuenta_por_cobrar

        registrar_pago_cxc_service(
            cuenta_id=cuenta.id,
            usuario=self.cajera,
            metodo='EFECTIVO',
            monto=Decimal('33.00'),
        )
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado, CuentaPorCobrar.ESTADO_PARCIAL)

        registrar_pago_cxc_service(
            cuenta_id=cuenta.id,
            usuario=self.cajera,
            metodo='EFECTIVO',
            monto=Decimal('66.00'),
        )
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado, CuentaPorCobrar.ESTADO_PAGADA)
        self.assertEqual(cuenta.saldo, Decimal('0.00'))

    def test_frecuencia_editable_por_venta_genera_fechas_semanales(self):
        # El metodo es MENSUAL, pero el POS manda frecuencia SEMANAL
        from datetime import date

        venta = self._vender(credito={'interes_porcentaje': '0', 'frecuencia': 'SEMANAL'})
        cuotas = list(venta.cuenta_por_cobrar.cuotas.order_by('numero'))

        self.assertEqual(
            [c.fecha_vencimiento for c in cuotas],
            [date(2026, 7, 15), date(2026, 7, 22), date(2026, 7, 29)],
        )

    def test_frecuencia_quincenal_editable_por_venta(self):
        from datetime import date

        venta = self._vender(credito={'interes_porcentaje': '0', 'frecuencia': 'QUINCENAL'})
        cuotas = list(venta.cuenta_por_cobrar.cuotas.order_by('numero'))

        self.assertEqual(
            [c.fecha_vencimiento for c in cuotas],
            [date(2026, 7, 15), date(2026, 7, 30), date(2026, 8, 14)],
        )

    def test_sin_frecuencia_usa_la_del_metodo(self):
        from datetime import date

        venta = self._vender(credito={'interes_porcentaje': '0'})
        cuotas = list(venta.cuenta_por_cobrar.cuotas.order_by('numero'))

        # metodo_cuotas es MENSUAL (30 dias)
        self.assertEqual(
            [c.fecha_vencimiento for c in cuotas],
            [date(2026, 7, 15), date(2026, 8, 14), date(2026, 9, 13)],
        )

    def test_frecuencia_invalida_rechaza_venta(self):
        with self.assertRaises(MetodoPlazoCreditoInvalidoError):
            self._vender(credito={'frecuencia': 'DIARIA'})

    def test_payload_sync_incluye_campos_de_interes(self):
        venta = self._vender(credito={'interes_porcentaje': '10'})
        payload = serializar_cxc(venta.cuenta_por_cobrar)

        self.assertEqual(payload['saldo_original'], '90.00')
        self.assertEqual(payload['interes_porcentaje'], '10.00')
        self.assertEqual(payload['monto_interes'], '9.00')
        self.assertEqual(payload['saldo'], '99.00')
        self.assertEqual(payload['modalidad'], MetodoPlazoCredito.TIPO_CUOTAS)
        self.assertEqual(payload['metodo_plazo_tipo'], MetodoPlazoCredito.TIPO_CUOTAS)
        self.assertEqual(payload['metodo_plazo_frecuencia'], MetodoPlazoCredito.FRECUENCIA_MENSUAL)
