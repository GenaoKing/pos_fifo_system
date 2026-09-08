from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.caja.models import Caja, MovimientoCaja, TurnoCaja
from apps.clientes.models import Cliente
from apps.cuentas_por_cobrar.models import CuentaPorCobrar, MetodoPlazoCredito, PagoCxC
from apps.negocios.models import Negocio
from apps.sucursales.models import Sucursal
from apps.sync.serializers import serializar_cierre_caja
from apps.ventas.models import Pago, Venta

Usuario = get_user_model()


class ResumenTurnoNotificacionesTests(TestCase):
    def test_snapshot_separa_ventas_cxc_metodos_y_movimientos(self):
        negocio = Negocio.objects.create(nombre='Resumen', slug='resumen')
        sucursal = Sucursal.objects.create(
            negocio=negocio, codigo='RES-1', nombre='Resumen 1',
        )
        usuario = Usuario.objects.create_user(
            'cajera_resumen', 'cajera_resumen@example.com', 'x',
            negocio=negocio,
        )
        caja = Caja.objects.create(nombre='Caja resumen', sucursal=sucursal)
        turno = TurnoCaja.objects.create(
            caja=caja, usuario=usuario, fondo_apertura=Decimal('100.00'),
        )

        contado = Venta.objects.create(
            numero_venta='RES-1-V1', fecha_venta=timezone.now(),
            usuario=usuario, sucursal=sucursal, subtotal=Decimal('150.00'),
            total=Decimal('150.00'), condicion_pago='CONTADO',
        )
        Pago.objects.create(
            venta=contado, metodo='EFECTIVO', monto=Decimal('100.00'),
            turno_caja=turno,
        )
        Pago.objects.create(
            venta=contado, metodo='TARJETA', monto=Decimal('50.00'),
            turno_caja=turno,
        )
        cliente = Cliente.objects.create(nombre='Cliente credito')
        credito = Venta.objects.create(
            numero_venta='RES-1-V2', fecha_venta=timezone.now(),
            usuario=usuario, sucursal=sucursal, cliente=cliente,
            subtotal=Decimal('200.00'), total=Decimal('200.00'),
            condicion_pago='CREDITO',
        )
        Pago.objects.create(
            venta=credito, metodo='CREDITO', monto=Decimal('200.00'),
            turno_caja=turno,
        )
        plazo = MetodoPlazoCredito.objects.create(nombre='30 dias resumen')
        cuenta = CuentaPorCobrar.objects.create(
            cliente=cliente, venta=credito, metodo_plazo=plazo,
            total=Decimal('200.00'), saldo_original=Decimal('200.00'),
            saldo=Decimal('200.00'),
            fecha_limite=timezone.localdate() + timedelta(days=30),
            creado_por=usuario, sucursal=sucursal,
        )
        PagoCxC.objects.create(
            cuenta=cuenta, metodo='EFECTIVO', monto=Decimal('40.00'),
            registrado_por=usuario, turno_caja=turno,
        )
        for tipo, monto in (
            ('RETIRO', '10.00'), ('GASTO', '5.00'), ('INGRESO', '2.00'),
        ):
            MovimientoCaja.objects.create(
                turno=turno, tipo=tipo, monto=Decimal(monto),
                descripcion=tipo, registrado_por=usuario,
            )

        turno.cerrar(Decimal('220.00'), usuario)
        resumen = turno.resumen_operativo()
        payload = serializar_cierre_caja(turno)

        self.assertEqual(resumen['cantidad_ventas'], 2)
        self.assertEqual(resumen['total_ventas'], Decimal('350.00'))
        self.assertEqual(resumen['pagos_por_metodo']['EFECTIVO'], Decimal('100.00'))
        self.assertEqual(resumen['pagos_por_metodo']['TARJETA'], Decimal('50.00'))
        self.assertEqual(resumen['pagos_por_metodo']['CREDITO'], Decimal('200.00'))
        self.assertEqual(resumen['cobros_cxc_total'], Decimal('40.00'))
        self.assertEqual(resumen['esperado'], Decimal('227.00'))
        self.assertEqual(resumen['diferencia'], Decimal('-7.00'))
        self.assertEqual(payload['schema_version'], 2)
        self.assertEqual(payload['resumen_turno']['total_ventas'], '350.00')
        self.assertEqual(payload['resumen_turno']['fuente_resumen'], 'pos_snapshot')

    def test_resumen_reutiliza_el_calculo_de_cerrar(self):
        # Al cerrar, el snapshot se calcula una sola vez: pasar el `calculo` de
        # cerrar() da lo mismo que recomputarlo, y el payload de sync con el
        # snapshot precalculado es identico al que lo recalcula.
        negocio = Negocio.objects.create(nombre='Reuso', slug='reuso')
        sucursal = Sucursal.objects.create(
            negocio=negocio, codigo='REU-1', nombre='Reuso 1',
        )
        usuario = Usuario.objects.create_user(
            'cajera_reuso', 'cajera_reuso@example.com', 'x', negocio=negocio,
        )
        caja = Caja.objects.create(nombre='Caja reuso', sucursal=sucursal)
        turno = TurnoCaja.objects.create(
            caja=caja, usuario=usuario, fondo_apertura=Decimal('50.00'),
        )
        venta = Venta.objects.create(
            numero_venta='REU-1-V1', fecha_venta=timezone.now(), usuario=usuario,
            sucursal=sucursal, subtotal=Decimal('30.00'), total=Decimal('30.00'),
            condicion_pago='CONTADO',
        )
        Pago.objects.create(
            venta=venta, metodo='EFECTIVO', monto=Decimal('30.00'),
            turno_caja=turno,
        )

        calculo = turno.cerrar(Decimal('80.00'), usuario)

        self.assertEqual(
            turno.resumen_operativo(efectivo=calculo),
            turno.resumen_operativo(),
        )
        resumen = turno.resumen_operativo(efectivo=calculo)
        self.assertEqual(
            serializar_cierre_caja(turno, resumen=resumen),
            serializar_cierre_caja(turno),
        )
