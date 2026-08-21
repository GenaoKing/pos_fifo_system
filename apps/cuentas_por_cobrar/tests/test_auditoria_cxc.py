"""
apps/cuentas_por_cobrar/tests/test_auditoria_cxc.py

Regresion de los hallazgos de
`docs/exploracion/AUDITORIA_CODIGO_APPS_CUENTAS_POR_COBRAR.md`.

La auditoria reprodujo cada defecto con una bateria temporal que despues
elimino. Estos tests son esa bateria hecha permanente.
"""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.clientes.models import Cliente
from apps.cuentas_por_cobrar.models import (
    CuentaPorCobrar,
    CuotaCxC,
    MetodoPlazoCredito,
    PagoCxC,
)
from apps.cuentas_por_cobrar.services import (
    anular_cuenta_por_venta,
    registrar_pago_cxc_service,
)
from apps.inventario.models import Compra, DetalleCompra
from apps.permisos import testing as permisos_testing
from apps.permisos.models import AutorizacionInvalida, AutorizacionOverride
from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal
from apps.ventas.services import (
    AnulacionConAbonosError,
    procesar_venta_service,
)

User = get_user_model()


class CxCTestCase(TestCase):
    """Fixture: un cajero habilitado, un cliente a credito y stock."""

    def setUp(self):
        cache.clear()

        self.admin = User.objects.create_user(
            username='admin_cxc_aud', email='admin_cxc_aud@test.local',
            password='pass', rol='ADMIN', activo=True,
        )
        self.cajera = User.objects.create_user(
            username='cajera_cxc_aud', email='cajera_cxc_aud@test.local',
            password='pass', rol='CAJERA', activo=True,
        )
        permisos_testing.habilitar_cajero(self.cajera)

        self.categoria = Categoria.objects.create(nombre='CxC Auditoria')
        self.producto = Producto.objects.create(
            sku='CXC-AUD-001', codigo_barras='CXC-AUD-001',
            nombre='Producto CxC', descripcion='', categoria=self.categoria,
            precio_venta=Decimal('100.00'), stock_minimo=1, activo=True,
            estado='nuevo', marca='', atributos={},
        )
        compra = Compra.objects.create(
            usuario=self.admin, proveedor='Proveedor CxC',
            numero_factura='FAC-CXC-AUD', total=Decimal('1000.00'),
        )
        DetalleCompra.objects.create(
            compra=compra, producto=self.producto, cantidad=50,
            costo_unitario=Decimal('20.00'), subtotal=Decimal('1000.00'),
        )

        self.cliente = Cliente.objects.create(
            tipo='CORPORATIVO', nombre='Cliente CxC Auditoria',
            cedula_rnc='131555001', limite_credito=Decimal('1000.00'),
            plazo_credito_dias=30, activo=True,
        )
        self.metodo = MetodoPlazoCredito.objects.create(
            nombre='Unico 30 auditoria',
            tipo=MetodoPlazoCredito.TIPO_VENCIMIENTO_UNICO,
            dias_vencimiento=30, cantidad_cuotas=1,
            frecuencia=MetodoPlazoCredito.FRECUENCIA_MENSUAL,
            inicial_minima_porcentaje=Decimal('0.00'), activo=True,
        )

    def tearDown(self):
        cache.clear()

    def _vender_credito(self, *, total='200.00', cantidad=2, credito=None):
        base = {
            'modalidad': 'VENCIMIENTO_UNICO',
            'metodo_plazo_id': self.metodo.id,
            'monto_inicial': '0.00',
            'metodo_inicial': 'efectivo',
        }
        base.update(credito or {})
        return procesar_venta_service(
            usuario=self.cajera,
            datos={
                'carrito': [{
                    'id': self.producto.id, 'cantidad': cantidad,
                    'precio_venta': '100.00', 'descuento': '0.00',
                }],
                'metodo_pago': 'credito',
                'cliente_id': self.cliente.id,
                'total': total,
                'credito': base,
            },
        )


class AutorizacionDeCreditoTests(CxCTestCase):
    """CXC-001: el override ya no se autoriza con un ID adivinable."""

    def test_la_autorizacion_se_consume_una_sola_vez(self):
        autorizacion, token = AutorizacionOverride.emitir(
            operacion=AutorizacionOverride.OP_CREDITO_EXCEDER_LIMITE,
            autorizado_por=self.admin,
            solicitado_por=self.cajera,
            monto_maximo=Decimal('500.00'),
            alcance={'cliente_id': self.cliente.id},
            motivo='Cliente historico',
        )

        consumida = AutorizacionOverride.consumir(
            token=token,
            operacion=AutorizacionOverride.OP_CREDITO_EXCEDER_LIMITE,
            solicitado_por=self.cajera,
            monto=Decimal('300.00'),
            alcance={'cliente_id': self.cliente.id},
            referencia='V-TEST',
        )
        self.assertEqual(consumida.pk, autorizacion.pk)

        with self.assertRaises(AutorizacionInvalida):
            AutorizacionOverride.consumir(
                token=token,
                operacion=AutorizacionOverride.OP_CREDITO_EXCEDER_LIMITE,
                solicitado_por=self.cajera,
            )

    def test_una_autorizacion_vencida_no_sirve(self):
        _, token = AutorizacionOverride.emitir(
            operacion=AutorizacionOverride.OP_CREDITO_EXCEDER_LIMITE,
            autorizado_por=self.admin, motivo='Vieja', minutos=5,
        )
        AutorizacionOverride.objects.update(
            expira=timezone.now() - timezone.timedelta(minutes=1)
        )

        with self.assertRaises(AutorizacionInvalida):
            AutorizacionOverride.consumir(
                token=token,
                operacion=AutorizacionOverride.OP_CREDITO_EXCEDER_LIMITE,
            )

    def test_una_autorizacion_de_otra_operacion_no_sirve(self):
        _, token = AutorizacionOverride.emitir(
            operacion=AutorizacionOverride.OP_CAJA_RETIRO,
            autorizado_por=self.admin, motivo='Retiro',
        )

        with self.assertRaises(AutorizacionInvalida):
            AutorizacionOverride.consumir(
                token=token,
                operacion=AutorizacionOverride.OP_CREDITO_EXCEDER_LIMITE,
            )

    def test_una_autorizacion_de_otro_operador_no_sirve(self):
        _, token = AutorizacionOverride.emitir(
            operacion=AutorizacionOverride.OP_CREDITO_EXCEDER_LIMITE,
            autorizado_por=self.admin, solicitado_por=self.cajera,
            motivo='Para la cajera',
        )
        otro = User.objects.create_user(
            username='otro_operador', email='otro@test.local',
            password='pass', rol='CAJERA', activo=True,
        )

        with self.assertRaises(AutorizacionInvalida):
            AutorizacionOverride.consumir(
                token=token,
                operacion=AutorizacionOverride.OP_CREDITO_EXCEDER_LIMITE,
                solicitado_por=otro,
            )

    def test_emitir_sin_motivo_falla(self):
        """`motivo_override` era opcional y podia quedar vacio."""
        with self.assertRaises(ValueError):
            AutorizacionOverride.emitir(
                operacion=AutorizacionOverride.OP_CREDITO_EXCEDER_LIMITE,
                autorizado_por=self.admin, motivo='   ',
            )


class LimiteDeCreditoTests(CxCTestCase):
    """CXC-002: el limite es una invariante, no una validacion informativa."""

    def test_el_limite_se_respeta_en_ventas_sucesivas(self):
        self.cliente.limite_credito = Decimal('250.00')
        self.cliente.save(update_fields=['limite_credito'])

        self._vender_credito(total='200.00', cantidad=2)

        from apps.ventas.services import LimiteCreditoExcedidoError

        with self.assertRaises(LimiteCreditoExcedidoError):
            self._vender_credito(total='200.00', cantidad=2)

    def test_el_saldo_nunca_supera_el_limite_sin_autorizacion(self):
        self.cliente.limite_credito = Decimal('250.00')
        self.cliente.save(update_fields=['limite_credito'])
        self._vender_credito(total='200.00', cantidad=2)

        saldo = CuentaPorCobrar.objects.filter(
            cliente=self.cliente,
            estado__in=CuentaPorCobrar.ESTADOS_ABIERTOS,
        ).aggregate(total=__import__('django').db.models.Sum('saldo'))['total']

        self.assertLessEqual(saldo, self.cliente.limite_credito)


class AnulacionConAbonosTests(CxCTestCase):
    """CXC-006: no se puede anular dejando dinero aplicado sin destino."""

    def test_anular_una_venta_con_abonos_se_bloquea(self):
        venta = self._vender_credito()
        cuenta = venta.cuenta_por_cobrar

        registrar_pago_cxc_service(
            cuenta_id=cuenta.id, usuario=self.admin,
            metodo='EFECTIVO', monto=Decimal('40.00'),
        )

        with self.assertRaises(AnulacionConAbonosError) as ctx:
            anular_cuenta_por_venta(venta=venta, usuario=self.admin)

        self.assertIn('40.00', str(ctx.exception))
        self.assertEqual(ctx.exception.status_code, 409)

        # Nada quedo a medias: la cuenta sigue viva y el abono aplicado.
        cuenta.refresh_from_db()
        self.assertNotEqual(cuenta.estado, CuentaPorCobrar.ESTADO_ANULADA)
        self.assertEqual(
            cuenta.pagos_cxc.filter(estado=PagoCxC.ESTADO_APLICADO).count(), 1
        )

    def test_sin_abonos_la_anulacion_procede(self):
        venta = self._vender_credito()

        cuenta = anular_cuenta_por_venta(venta=venta, usuario=self.admin)

        self.assertEqual(cuenta.estado, CuentaPorCobrar.ESTADO_ANULADA)
        self.assertEqual(cuenta.saldo, Decimal('0.00'))

    def test_revirtiendo_el_abono_primero_si_se_puede_anular(self):
        """El camino que el error indica al operador."""
        from apps.cuentas_por_cobrar.services import anular_pago_cxc_service

        venta = self._vender_credito()
        cuenta = venta.cuenta_por_cobrar
        pago = registrar_pago_cxc_service(
            cuenta_id=cuenta.id, usuario=self.admin,
            metodo='EFECTIVO', monto=Decimal('40.00'),
        )

        anular_pago_cxc_service(
            pago_id=pago.id, usuario=self.admin,
            motivo='Reversa previa a la anulacion de la venta',
        )
        cuenta.refresh_from_db()

        anulada = anular_cuenta_por_venta(venta=venta, usuario=self.admin)
        self.assertEqual(anulada.estado, CuentaPorCobrar.ESTADO_ANULADA)


class CuotasTests(CxCTestCase):
    """CXC-007: ninguna cuota puede quedar negativa."""

    def setUp(self):
        super().setUp()
        self.metodo_cuotas = MetodoPlazoCredito.objects.create(
            nombre='Cuotas auditoria',
            tipo=MetodoPlazoCredito.TIPO_CUOTAS,
            dias_vencimiento=30, cantidad_cuotas=3,
            frecuencia=MetodoPlazoCredito.FRECUENCIA_MENSUAL,
            inicial_minima_porcentaje=Decimal('0.00'), activo=True,
        )

    def test_el_reparto_no_produce_cuotas_negativas(self):
        from apps.cuentas_por_cobrar.services import _montos_cuotas

        for saldo, cantidad in [
            (Decimal('1.00'), 1),
            (Decimal('1.00'), 2),
            (Decimal('1.00'), 100),
            (Decimal('100.00'), 3),
            (Decimal('0.07'), 7),
        ]:
            with self.subTest(saldo=saldo, cantidad=cantidad):
                montos = _montos_cuotas(saldo, cantidad)
                self.assertEqual(len(montos), cantidad)
                self.assertTrue(
                    all(m > Decimal('0.00') for m in montos),
                    f'cuota no positiva en {montos}',
                )
                self.assertEqual(sum(montos), saldo)

    def test_un_saldo_que_no_alcanza_para_todas_las_cuotas_es_error(self):
        """1.00 en 200 cuotas producia 199 de 0.01 y una final de -0.99."""
        from apps.cuentas_por_cobrar.services import _montos_cuotas
        from apps.ventas.services import MetodoPlazoCreditoInvalidoError

        with self.assertRaises(MetodoPlazoCreditoInvalidoError):
            _montos_cuotas(Decimal('1.00'), 200)

    def test_hay_un_tope_de_cuotas(self):
        with self.assertRaises(ValueError):
            self.metodo_cuotas.normalizar_cantidad_cuotas(
                MetodoPlazoCredito.MAX_CUOTAS + 1
            )

    def test_el_tope_permite_el_maximo_exacto(self):
        self.assertEqual(
            self.metodo_cuotas.normalizar_cantidad_cuotas(
                MetodoPlazoCredito.MAX_CUOTAS
            ),
            MetodoPlazoCredito.MAX_CUOTAS,
        )


class IdempotenciaCobroTests(CxCTestCase):
    """CXC-009: un reintento no puede cobrar dos veces."""

    def test_dos_llamadas_con_la_misma_clave_crean_un_solo_abono(self):
        venta = self._vender_credito()
        cuenta = venta.cuenta_por_cobrar
        saldo_inicial = cuenta.saldo

        primero = registrar_pago_cxc_service(
            cuenta_id=cuenta.id, usuario=self.admin, metodo='EFECTIVO',
            monto=Decimal('50.00'), clave_idempotencia='op-abc-123',
        )
        segundo = registrar_pago_cxc_service(
            cuenta_id=cuenta.id, usuario=self.admin, metodo='EFECTIVO',
            monto=Decimal('50.00'), clave_idempotencia='op-abc-123',
        )

        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(cuenta.pagos_cxc.count(), 1)

        cuenta.refresh_from_db()
        self.assertEqual(cuenta.saldo, saldo_inicial - Decimal('50.00'))

    def test_sin_clave_se_conserva_la_conducta_anterior(self):
        """Los callers que todavia no la envian siguen funcionando."""
        venta = self._vender_credito()
        cuenta = venta.cuenta_por_cobrar

        registrar_pago_cxc_service(
            cuenta_id=cuenta.id, usuario=self.admin,
            metodo='EFECTIVO', monto=Decimal('10.00'),
        )
        registrar_pago_cxc_service(
            cuenta_id=cuenta.id, usuario=self.admin,
            metodo='EFECTIVO', monto=Decimal('10.00'),
        )

        self.assertEqual(cuenta.pagos_cxc.count(), 2)

    def test_el_endpoint_acepta_la_clave(self):
        venta = self._vender_credito()
        cuenta = venta.cuenta_por_cobrar
        permisos_testing.habilitar_cajero(
            self.admin, permisos=['cuentas_por_cobrar.cobrar', 'cuentas_por_cobrar.ver'],
        )
        self.client.force_login(self.admin)

        cuerpo = {
            'cuenta_id': cuenta.id, 'metodo': 'EFECTIVO', 'monto': '25.00',
            'clave_idempotencia': 'endpoint-xyz',
        }
        for _ in range(2):
            resp = self.client.post(
                reverse('cuentas_por_cobrar:api_registrar_pago'),
                data=json.dumps(cuerpo), content_type='application/json',
            )
            self.assertEqual(resp.status_code, 200, resp.content)

        self.assertEqual(cuenta.pagos_cxc.count(), 1)


class VencimientoEfectivoTests(CxCTestCase):
    """CXC-012: una cuenta vencida de hecho aparece como vencida."""

    def test_el_filtro_vencida_encuentra_la_deuda_ya_vencida(self):
        venta = self._vender_credito()
        cuenta = venta.cuenta_por_cobrar

        # Cruza la fecha limite sin ningun movimiento posterior: `estado`
        # sigue siendo ABIERTA porque solo se recalcula por eventos.
        CuentaPorCobrar.objects.filter(pk=cuenta.pk).update(
            fecha_limite=timezone.localdate() - timezone.timedelta(days=5),
        )
        cuenta.refresh_from_db()
        self.assertTrue(cuenta.esta_vencida)
        self.assertNotEqual(cuenta.estado, CuentaPorCobrar.ESTADO_VENCIDA)

        permisos_testing.habilitar_cajero(
            self.admin, permisos=['cuentas_por_cobrar.ver'],
        )
        self.client.force_login(self.admin)
        resp = self.client.get(
            reverse('cuentas_por_cobrar:lista'), {'estado': 'VENCIDA'},
        )

        self.assertEqual(resp.status_code, 200)
        cuentas = json.loads(resp.context['cuentas_json'])
        self.assertIn(cuenta.id, [c['id'] for c in cuentas])
        self.assertEqual(cuentas[0]['estado_efectivo'], 'VENCIDA')


class ExportacionSeguraTests(CxCTestCase):
    """CXC-014: los datos de usuario no pueden convertirse en formulas."""

    def test_neutraliza_los_prefijos_de_formula(self):
        from apps.cuentas_por_cobrar.excel_generator import texto_seguro

        for entrada in ('=1+1', '+1', '-1', '@SUM(A1)'):
            with self.subTest(entrada=entrada):
                self.assertTrue(texto_seguro(entrada).startswith("'"))

    def test_no_toca_el_texto_normal(self):
        from apps.cuentas_por_cobrar.excel_generator import texto_seguro

        self.assertEqual(texto_seguro('Juan Perez'), 'Juan Perez')
        self.assertEqual(texto_seguro(None), '')


class AdminInmutableTests(CxCTestCase):
    """CXC-008: el admin no puede saltarse los services."""

    def test_cuentas_cuotas_y_pagos_son_de_solo_lectura(self):
        from django.contrib import admin as django_admin

        from apps.cuentas_por_cobrar.admin import (
            CuentaPorCobrarAdmin,
            CuotaCxCAdmin,
            PagoCxCAdmin,
        )

        for clase, modelo in (
            (CuentaPorCobrarAdmin, CuentaPorCobrar),
            (CuotaCxCAdmin, CuotaCxC),
            (PagoCxCAdmin, PagoCxC),
        ):
            with self.subTest(admin=clase.__name__):
                instancia = clase(modelo, django_admin.site)
                self.assertFalse(instancia.has_add_permission(None))
                self.assertFalse(instancia.has_change_permission(None))
                self.assertFalse(instancia.has_delete_permission(None))

    def test_el_catalogo_de_metodos_si_es_editable(self):
        """Un metodo de plazo es configuracion, no un hecho contable."""
        from django.contrib import admin as django_admin

        from apps.cuentas_por_cobrar.admin import (
            CuentaPorCobrarAdmin,
            MetodoPlazoCreditoAdmin,
        )

        instancia = MetodoPlazoCreditoAdmin(MetodoPlazoCredito, django_admin.site)

        # No hereda el mixin de solo lectura: usa el permiso estandar de Django.
        self.assertFalse(
            hasattr(MetodoPlazoCreditoAdmin, 'has_change_permission')
            and MetodoPlazoCreditoAdmin.has_change_permission
            is CuentaPorCobrarAdmin.has_change_permission
        )


class FlujoOverrideEndToEndTests(CxCTestCase):
    """
    El camino real del POS: pedir la autorizacion al endpoint y usarla en la
    venta. Cubre el cableado completo endpoint -> token -> service.
    """

    def setUp(self):
        super().setUp()
        permisos_testing.habilitar_cajero(
            self.admin,
            permisos=['cuentas_por_cobrar.autorizar_exceso_credito'],
        )
        self.cliente.limite_credito = Decimal('100.00')
        self.cliente.save(update_fields=['limite_credito'])
        self.client.force_login(self.cajera)

    def _pedir_autorizacion(self, **over):
        cuerpo = {
            'username': self.admin.username,
            'password': 'pass',
            'operacion': 'credito.exceder_limite',
            'motivo': 'Cliente historico, aprobado por gerencia',
            'monto': '200.00',
            'cliente_id': self.cliente.id,
        }
        cuerpo.update(over)
        return self.client.post(
            reverse('caja:api_validar_admin'),
            data=json.dumps(cuerpo), content_type='application/json',
        )

    def test_el_endpoint_emite_un_token_y_la_venta_lo_consume(self):
        resp = self._pedir_autorizacion()

        self.assertEqual(resp.status_code, 200, resp.content)
        datos = resp.json()
        self.assertTrue(datos['valido'])
        self.assertIn('token', datos)
        # El id del admin ya NO viaja: era lo que hacia falsificable el override.
        self.assertNotIn('admin_id', datos)

        venta = self._vender_credito(
            credito={'override_token': datos['token']},
        )

        cuenta = venta.cuenta_por_cobrar
        self.assertEqual(cuenta.override_autorizado_por, self.admin)
        self.assertEqual(
            cuenta.motivo_override, 'Cliente historico, aprobado por gerencia',
        )

    def test_sin_motivo_el_endpoint_no_emite_nada(self):
        resp = self._pedir_autorizacion(motivo='')

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['valido'])
        self.assertEqual(AutorizacionOverride.objects.count(), 0)

    def test_con_password_incorrecta_no_emite_nada(self):
        resp = self._pedir_autorizacion(password='incorrecta')

        self.assertFalse(resp.json()['valido'])
        self.assertEqual(AutorizacionOverride.objects.count(), 0)

    def test_un_usuario_sin_el_permiso_no_puede_autorizar(self):
        sin_permiso = User.objects.create_user(
            username='cajero_pelado', email='pelado@test.local',
            password='pass', rol='CAJERA', activo=True,
        )

        resp = self._pedir_autorizacion(username=sin_permiso.username)

        self.assertFalse(resp.json()['valido'])
        self.assertIn('permiso', resp.json()['error'].lower())
        self.assertEqual(AutorizacionOverride.objects.count(), 0)
