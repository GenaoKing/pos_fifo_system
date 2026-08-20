from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.caja.models import Caja, TurnoCaja
from apps.clientes.models import Cliente
from apps.cuentas_por_cobrar.models import CuentaPorCobrar, CuotaCxC, MetodoPlazoCredito, PagoCxC
from apps.cuentas_por_cobrar.services import anular_pago_cxc_service, registrar_pago_cxc_service
from apps.inventario.models import Compra, DetalleCompra
from apps.productos.models import Categoria, Producto
from apps.sync.models import EventoSync
from apps.sync.serializers import serializar_anulacion_pago_cxc
from apps.ventas.services import MetodoPlazoCreditoInvalidoError, procesar_venta_service


class AnulacionPagoCxCTestsBase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='admin_anul',
            email='admin_anul@test.local',
            password='pass',
            rol='ADMIN',
            activo=True,
        )
        self.cajera = User.objects.create_user(
            username='cajera_anul',
            email='cajera_anul@test.local',
            password='pass',
            rol='CAJERA',
            activo=True,
        )
        self.categoria = Categoria.objects.create(nombre='Anulacion Test')
        self.producto = Producto.objects.create(
            sku='ANUL-PROD-001',
            codigo_barras='ANUL-PROD-001',
            nombre='Producto anulacion',
            descripcion='',
            categoria=self.categoria,
            precio_venta=Decimal('100.00'),
            stock_minimo=1,
            activo=True,
            estado='nuevo',
            marca='',
            atributos={},
        )
        compra = Compra.objects.create(
            usuario=self.admin,
            proveedor='Proveedor Anul',
            numero_factura='FAC-ANUL-001',
            total=Decimal('1000.00'),
        )
        DetalleCompra.objects.create(
            compra=compra,
            producto=self.producto,
            cantidad=10,
            costo_unitario=Decimal('50.00'),
            subtotal=Decimal('500.00'),
        )
        self.cliente = Cliente.objects.create(
            tipo='CORPORATIVO',
            nombre='Cliente Anulacion',
            cedula_rnc='131999003',
            limite_credito=Decimal('1000.00'),
            activo=True,
        )
        self.metodo_cuotas = MetodoPlazoCredito.objects.create(
            nombre='3 cuotas anulacion test',
            tipo=MetodoPlazoCredito.TIPO_CUOTAS,
            dias_vencimiento=15,
            cantidad_cuotas=3,
            frecuencia=MetodoPlazoCredito.FRECUENCIA_MENSUAL,
            inicial_minima_porcentaje=Decimal('0.00'),
            activo=True,
        )

    def _crear_cuenta(self):
        venta = procesar_venta_service(
            usuario=self.cajera,
            datos={
                'carrito': [
                    {
                        'id': self.producto.id,
                        'cantidad': 1,
                        'precio_venta': '100.00',
                        'descuento': '0.00',
                    }
                ],
                'metodo_pago': 'credito',
                'cliente_id': self.cliente.id,
                'total': '100.00',
                'tipo_ecf': '31',
                'credito': {
                    'metodo_plazo_id': self.metodo_cuotas.id,
                    # Fecha RELATIVA: con una fija, el test empieza a fallar
                    # solo por el paso del tiempo (la cuota se vuelve VENCIDA
                    # y deja de estar PENDIENTE como espera el assert).
                    'fecha_primer_vencimiento': (
                        timezone.localdate() + timedelta(days=30)
                    ).isoformat(),
                    'monto_inicial': '10.00',
                    'metodo_inicial': 'efectivo',
                    'cantidad_cuotas': 3,
                    'interes_porcentaje': '0',
                },
            },
        )
        return venta.cuenta_por_cobrar

    def _abonar(self, cuenta, monto):
        return registrar_pago_cxc_service(
            cuenta_id=cuenta.id,
            usuario=self.cajera,
            metodo='EFECTIVO',
            monto=Decimal(monto),
        )


class AnulacionPagoCxCServiceTests(AnulacionPagoCxCTestsBase):
    def test_reversa_restituye_saldos_y_estados(self):
        # saldo 90 en 3 cuotas de 30; abono de 40 paga cuota 1 y deja cuota 2 en 20
        cuenta = self._crear_cuenta()
        pago = self._abonar(cuenta, '40.00')

        anulado = anular_pago_cxc_service(
            pago_id=pago.id,
            usuario=self.admin,
            motivo='Cobro duplicado',
        )

        cuenta.refresh_from_db()
        cuotas = list(CuotaCxC.objects.filter(cuenta=cuenta).order_by('numero'))

        self.assertEqual(anulado.estado, PagoCxC.ESTADO_ANULADO)
        self.assertEqual(anulado.anulado_por, self.admin)
        self.assertEqual(anulado.motivo_anulacion, 'Cobro duplicado')
        self.assertEqual(cuenta.saldo, Decimal('90.00'))
        self.assertEqual(cuenta.estado, CuentaPorCobrar.ESTADO_ABIERTA)
        self.assertEqual(cuotas[0].saldo, Decimal('30.00'))
        self.assertEqual(cuotas[0].estado, CuotaCxC.ESTADO_PENDIENTE)
        self.assertIsNone(cuotas[0].fecha_pago)
        self.assertEqual(cuotas[1].saldo, Decimal('30.00'))
        self.assertEqual(cuotas[1].estado, CuotaCxC.ESTADO_PENDIENTE)

    def test_cuenta_pagada_vuelve_a_abierta_al_anular_ultimo_pago(self):
        cuenta = self._crear_cuenta()
        self._abonar(cuenta, '50.00')
        pago_final = self._abonar(cuenta, '40.00')
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado, CuentaPorCobrar.ESTADO_PAGADA)

        anular_pago_cxc_service(pago_id=pago_final.id, usuario=self.admin, motivo='Error de caja')

        cuenta.refresh_from_db()
        self.assertEqual(cuenta.saldo, Decimal('40.00'))
        self.assertEqual(cuenta.estado, CuentaPorCobrar.ESTADO_PARCIAL)

    def test_rechaza_anular_pago_que_no_es_el_ultimo(self):
        cuenta = self._crear_cuenta()
        primero = self._abonar(cuenta, '30.00')
        self._abonar(cuenta, '20.00')

        with self.assertRaises(MetodoPlazoCreditoInvalidoError):
            anular_pago_cxc_service(pago_id=primero.id, usuario=self.admin, motivo='x')

    def test_rechaza_doble_anulacion(self):
        cuenta = self._crear_cuenta()
        pago = self._abonar(cuenta, '30.00')
        anular_pago_cxc_service(pago_id=pago.id, usuario=self.admin, motivo='primera')

        with self.assertRaises(MetodoPlazoCreditoInvalidoError):
            anular_pago_cxc_service(pago_id=pago.id, usuario=self.admin, motivo='segunda')

    def test_rechaza_sin_motivo(self):
        cuenta = self._crear_cuenta()
        pago = self._abonar(cuenta, '30.00')

        with self.assertRaises(MetodoPlazoCreditoInvalidoError):
            anular_pago_cxc_service(pago_id=pago.id, usuario=self.admin, motivo='   ')

    def test_anulacion_en_cadena_permite_revertir_pagos_anteriores(self):
        cuenta = self._crear_cuenta()
        primero = self._abonar(cuenta, '30.00')
        segundo = self._abonar(cuenta, '20.00')

        anular_pago_cxc_service(pago_id=segundo.id, usuario=self.admin, motivo='cadena 1')
        anular_pago_cxc_service(pago_id=primero.id, usuario=self.admin, motivo='cadena 2')

        cuenta.refresh_from_db()
        self.assertEqual(cuenta.saldo, Decimal('90.00'))

    def test_turno_abierto_descuenta_pago_anulado_del_esperado(self):
        caja = Caja.objects.create(nombre='Caja Test Anul', activa=True)
        turno = TurnoCaja.objects.create(
            caja=caja,
            usuario=self.cajera,
            fondo_apertura=Decimal('100.00'),
        )
        cuenta = self._crear_cuenta()
        pago = self._abonar(cuenta, '40.00')

        esperado_con_pago = turno.calcular_esperado()
        anular_pago_cxc_service(pago_id=pago.id, usuario=self.admin, motivo='cuadre')
        esperado_sin_pago = turno.calcular_esperado()

        self.assertEqual(esperado_con_pago['efectivo_cxc'] - esperado_sin_pago['efectivo_cxc'], Decimal('40.00'))
        self.assertEqual(esperado_con_pago['esperado'] - esperado_sin_pago['esperado'], Decimal('40.00'))

    @override_settings(SYNC_ENABLED=True)
    def test_encola_evento_sync_cxc_pago_anulado(self):
        cuenta = self._crear_cuenta()
        pago = self._abonar(cuenta, '30.00')

        with self.captureOnCommitCallbacks(execute=True):
            anular_pago_cxc_service(pago_id=pago.id, usuario=self.admin, motivo='sync')

        evento = EventoSync.objects.filter(tipo_evento='CXC_PAGO_ANULADO').first()
        self.assertIsNotNone(evento)
        self.assertEqual(evento.payload['motivo_anulacion'], 'sync')
        self.assertEqual(evento.payload['monto'], '30.00')


class AnulacionPagoCxCHandlerCloudTests(AnulacionPagoCxCTestsBase):
    """El handler cloud opera sobre los mismos modelos: se prueba en la misma BD."""

    def test_handler_aplica_snapshot_y_es_idempotente(self):
        from apps.api.views.sync import _handler_cxc_pago_anulado

        cuenta = self._crear_cuenta()
        pago = self._abonar(cuenta, '40.00')
        anular_pago_cxc_service(pago_id=pago.id, usuario=self.admin, motivo='replica')
        pago.refresh_from_db()
        payload = serializar_anulacion_pago_cxc(pago)

        # Estado previo "cloud": revertir la anulacion localmente para simular
        # un cloud que solo recibio el CXC_PAGO_REGISTRADO.
        pago.estado = PagoCxC.ESTADO_APLICADO
        pago.anulado_por = None
        pago.fecha_anulacion = None
        pago.motivo_anulacion = ''
        pago.save()
        cuenta.refresh_from_db()
        cuenta.saldo = Decimal('50.00')
        cuenta.estado = CuentaPorCobrar.ESTADO_PARCIAL
        cuenta.save()

        _handler_cxc_pago_anulado(None, payload)

        pago.refresh_from_db()
        cuenta.refresh_from_db()
        self.assertEqual(pago.estado, PagoCxC.ESTADO_ANULADO)
        self.assertEqual(pago.motivo_anulacion, 'replica')
        self.assertEqual(cuenta.saldo, Decimal('90.00'))
        cuotas = list(cuenta.cuotas.order_by('numero'))
        self.assertEqual(cuotas[0].saldo, Decimal('30.00'))

        # Idempotencia: re-aplicar el mismo evento no cambia nada ni lanza
        _handler_cxc_pago_anulado(None, payload)
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.saldo, Decimal('90.00'))

    def test_handler_reintenta_si_pago_no_replico(self):
        from apps.api.views.sync import _handler_cxc_pago_anulado

        cuenta = self._crear_cuenta()
        pago = self._abonar(cuenta, '40.00')
        anular_pago_cxc_service(pago_id=pago.id, usuario=self.admin, motivo='replica')
        pago.refresh_from_db()
        payload = serializar_anulacion_pago_cxc(pago)
        pago.delete()

        with self.assertRaises(ValueError):
            _handler_cxc_pago_anulado(None, payload)


class AnulacionPagoCxCPermisosTests(AnulacionPagoCxCTestsBase):
    def test_endpoint_anular_devuelve_403_sin_permiso(self):
        cuenta = self._crear_cuenta()
        pago = self._abonar(cuenta, '30.00')

        # CAJERA sin roles asignados: default deny en el motor RBAC
        self.client.force_login(self.cajera)
        res = self.client.post(
            f'/cuentas-por-cobrar/api/pago/{pago.id}/anular/',
            data='{"motivo": "no deberia"}',
            content_type='application/json',
        )
        self.assertEqual(res.status_code, 403)
        pago.refresh_from_db()
        self.assertEqual(pago.estado, PagoCxC.ESTADO_APLICADO)

    def test_endpoint_anular_funciona_con_acceso_total(self):
        User = get_user_model()
        sysadmin = User.objects.create_user(
            username='sysadmin_anul',
            email='sysadmin_anul@test.local',
            password='pass',
            rol='SYSADMIN',
            activo=True,
        )
        cuenta = self._crear_cuenta()
        pago = self._abonar(cuenta, '30.00')

        self.client.force_login(sysadmin)
        res = self.client.post(
            f'/cuentas-por-cobrar/api/pago/{pago.id}/anular/',
            data='{"motivo": "autorizado"}',
            content_type='application/json',
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['cuenta']['saldo'], '90.00')

    def test_registrar_pago_endpoint_requiere_permiso_cobrar(self):
        cuenta = self._crear_cuenta()

        self.client.force_login(self.cajera)
        res = self.client.post(
            '/cuentas-por-cobrar/api/pago/',
            data=f'{{"cuenta_id": {cuenta.id}, "metodo": "EFECTIVO", "monto": "10.00"}}',
            content_type='application/json',
        )
        self.assertEqual(res.status_code, 403)
