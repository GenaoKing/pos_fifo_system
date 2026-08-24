"""
Agregados diarios de la Fase 3 (anti-entropia): `apps/sync/resumen.py`.

Lo que importa probar: que agrupa por la fecha de DOMINIO correcta (no por
fecha_creacion), que la frontera de medianoche respeta la zona horaria pedida
-- no la del servidor -- y que la comparacion detecta exactamente lo que debe
detectar sin falsos positivos.
"""
from datetime import date, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.clientes.models import Cliente
from apps.cuentas_por_cobrar.models import CuentaPorCobrar, MetodoPlazoCredito, PagoCxC
from apps.sucursales.models import Sucursal
from apps.sync.resumen import TZInvalidaError, calcular_resumen, comparar_resumenes, resolver_zona
from apps.ventas.models import Venta

User = get_user_model()


class ResolverZonaTests(TestCase):
    def test_zona_valida(self):
        self.assertEqual(str(resolver_zona('America/Santo_Domingo')), 'America/Santo_Domingo')

    def test_zona_invalida_da_error_claro(self):
        with self.assertRaises(TZInvalidaError):
            resolver_zona('no/existe')


class CalcularResumenVentasTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            username='cajera_resumen', email='r@test.local', password='x', rol='CAJERA',
        )
        self.sucursal = Sucursal.objects.create(codigo='RS-001', nombre='RS', activa=True)

    def _venta(self, numero, fecha_venta, total='100.00', estado='COMPLETADA'):
        return Venta.objects.create(
            numero_venta=numero, fecha_venta=fecha_venta, usuario=self.usuario,
            sucursal=self.sucursal, total=Decimal(total), estado=estado,
        )

    def test_agrupa_por_fecha_venta_no_por_creacion(self):
        tz = timezone.get_current_timezone()
        fecha = timezone.make_aware(timezone.datetime(2026, 8, 18, 15, 0), tz)
        self._venta('V-1', fecha, total='100.00')
        self._venta('V-2', fecha, total='250.00')

        resumen = calcular_resumen(date(2026, 8, 18), date(2026, 8, 18), 'America/Santo_Domingo')
        fila = resumen['ventas']['2026-08-18']
        self.assertEqual(fila['count'], 2)
        self.assertEqual(fila['suma'], '350.00')
        self.assertEqual(fila['anuladas'], 0)

    def test_cuenta_anuladas_por_separado_sin_excluirlas_del_total(self):
        tz = timezone.get_current_timezone()
        fecha = timezone.make_aware(timezone.datetime(2026, 8, 18, 10, 0), tz)
        self._venta('V-1', fecha, total='100.00')
        self._venta('V-2', fecha, total='50.00', estado='ANULADA')

        resumen = calcular_resumen(date(2026, 8, 18), date(2026, 8, 18), 'America/Santo_Domingo')
        fila = resumen['ventas']['2026-08-18']
        self.assertEqual(fila['count'], 2, 'la venta anulada sigue contando: existe en ambos lados')
        self.assertEqual(fila['anuladas'], 1)

    def test_frontera_de_medianoche_respeta_la_zona_pedida_no_la_del_servidor(self):
        """
        23:30 en Santo Domingo (UTC-4) es ya las 03:30 UTC del dia siguiente.
        Pedir el resumen en UTC debe caer en el dia SIGUIENTE; pedirlo en
        America/Santo_Domingo debe caer en el dia de la venta.
        """
        fecha_utc = timezone.datetime(2026, 8, 19, 3, 30, tzinfo=dt_timezone.utc)
        self._venta('V-1', fecha_utc, total='100.00')

        resumen_local = calcular_resumen(date(2026, 8, 18), date(2026, 8, 18), 'America/Santo_Domingo')
        self.assertEqual(resumen_local['ventas']['2026-08-18']['count'], 1)

        resumen_utc = calcular_resumen(date(2026, 8, 19), date(2026, 8, 19), 'UTC')
        self.assertEqual(resumen_utc['ventas']['2026-08-19']['count'], 1)

    def test_dia_sin_ventas_no_aparece(self):
        resumen = calcular_resumen(date(2026, 8, 1), date(2026, 8, 3), 'America/Santo_Domingo')
        self.assertEqual(resumen['ventas'], {})

    def test_max_ref_es_el_correlativo_mas_alto_del_dia(self):
        tz = timezone.get_current_timezone()
        fecha = timezone.make_aware(timezone.datetime(2026, 8, 18, 9, 0), tz)
        self._venta('V-20260818-0001', fecha)
        self._venta('V-20260818-0009', fecha)
        self._venta('V-20260818-0003', fecha)

        resumen = calcular_resumen(date(2026, 8, 18), date(2026, 8, 18), 'America/Santo_Domingo')
        self.assertEqual(resumen['ventas']['2026-08-18']['max_ref'], 'V-20260818-0009')


class CalcularResumenCxcTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            username='admin_resumen_cxc', email='rc@test.local', password='x', rol='ADMIN',
        )
        self.sucursal = Sucursal.objects.create(codigo='RC-001', nombre='RC', activa=True)
        self.cliente = Cliente.objects.create(
            tipo='PERSONAL', nombre='Cliente Resumen', cedula_rnc='40200009999', activo=True,
        )
        self.metodo = MetodoPlazoCredito.objects.create(
            nombre='30 dias resumen', dias_vencimiento=30, activo=True,
        )

    def _cuenta(self, numero, fecha_emision, total, saldo):
        venta = Venta.objects.create(
            numero_venta=numero, fecha_venta=timezone.now(), usuario=self.usuario,
            cliente=self.cliente, sucursal=self.sucursal, total=Decimal(total),
            condicion_pago='CREDITO', estado='COMPLETADA',
        )
        return CuentaPorCobrar.objects.create(
            cliente=self.cliente, venta=venta, metodo_plazo=self.metodo,
            total=Decimal(total), saldo=Decimal(saldo), saldo_original=Decimal(total),
            fecha_emision=fecha_emision,
            fecha_limite=fecha_emision + timedelta(days=30),
            creado_por=self.usuario, sucursal=self.sucursal,
        )

    def test_agrupa_por_fecha_emision_sin_conversion_de_zona(self):
        """
        fecha_emision es un DateField ya local (timezone.localdate() al
        crearse): se agrupa tal cual, sin TruncDate ni tz.
        """
        self._cuenta('V-CXC-1', date(2026, 8, 18), '1000.00', '1000.00')
        self._cuenta('V-CXC-2', date(2026, 8, 18), '500.00', '200.00')

        resumen = calcular_resumen(date(2026, 8, 18), date(2026, 8, 18), 'America/Santo_Domingo')
        fila = resumen['cxc']['2026-08-18']
        self.assertEqual(fila['count'], 2)
        self.assertEqual(fila['saldo'], '1200.00')


class CalcularResumenPagosTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            username='admin_resumen_pago', email='rp@test.local', password='x', rol='ADMIN',
        )
        self.sucursal = Sucursal.objects.create(codigo='RP-001', nombre='RP', activa=True)
        self.cliente = Cliente.objects.create(
            tipo='PERSONAL', nombre='Cliente Pago', cedula_rnc='40200008888', activo=True,
        )
        self.metodo = MetodoPlazoCredito.objects.create(
            nombre='30 dias pago', dias_vencimiento=30, activo=True,
        )
        venta = Venta.objects.create(
            numero_venta='V-PAGO-1', fecha_venta=timezone.now(), usuario=self.usuario,
            cliente=self.cliente, sucursal=self.sucursal, total=Decimal('1000.00'),
            condicion_pago='CREDITO', estado='COMPLETADA',
        )
        self.cuenta = CuentaPorCobrar.objects.create(
            cliente=self.cliente, venta=venta, metodo_plazo=self.metodo,
            total=Decimal('1000.00'), saldo=Decimal('600.00'), saldo_original=Decimal('1000.00'),
            fecha_limite=timezone.localdate() + timedelta(days=30),
            creado_por=self.usuario, sucursal=self.sucursal,
        )

    def test_monto_solo_cuenta_pagos_aplicados(self):
        tz = timezone.get_current_timezone()
        fecha = timezone.make_aware(timezone.datetime(2026, 8, 18, 11, 0), tz)
        PagoCxC.objects.create(
            cuenta=self.cuenta, metodo='EFECTIVO', monto=Decimal('300.00'),
            registrado_por=self.usuario, fecha_pago=fecha,
        )
        PagoCxC.objects.create(
            cuenta=self.cuenta, metodo='EFECTIVO', monto=Decimal('999.00'),
            registrado_por=self.usuario, fecha_pago=fecha, estado=PagoCxC.ESTADO_ANULADO,
        )

        resumen = calcular_resumen(date(2026, 8, 18), date(2026, 8, 18), 'America/Santo_Domingo')
        fila = resumen['cxc_pagos']['2026-08-18']
        self.assertEqual(fila['count'], 2, 'el conteo total delata una anulacion que no replico')
        self.assertEqual(fila['monto'], '300.00', 'el monto excluye el pago anulado')


class CompararResumenesTests(TestCase):
    def test_sin_diferencias_no_reporta_nada(self):
        resumen = {'ventas': {'2026-08-18': {'count': 2, 'suma': '350.00', 'anuladas': 0}}}
        self.assertEqual(comparar_resumenes(resumen, dict(resumen)), [])

    def test_dia_faltante_en_cloud_es_divergencia(self):
        local = {'ventas': {'2026-08-18': {'count': 2, 'suma': '350.00', 'anuladas': 0}}}
        cloud = {'ventas': {}}
        divergencias = comparar_resumenes(local, cloud)
        tipos = {(d['tipo'], d['dia'], d['campo']) for d in divergencias}
        self.assertIn(('ventas', '2026-08-18', 'count'), tipos)
        self.assertIn(('ventas', '2026-08-18', 'suma'), tipos)

    def test_dia_sobrante_en_cloud_tambien_es_divergencia(self):
        """El cloud con MAS que la sucursal es tan anomalo como con menos."""
        local = {'ventas': {}}
        cloud = {'ventas': {'2026-08-18': {'count': 1, 'suma': '100.00', 'anuladas': 0}}}
        divergencias = comparar_resumenes(local, cloud)
        self.assertTrue(any(d['tipo'] == 'ventas' and d['dia'] == '2026-08-18' for d in divergencias))

    def test_diferencia_de_un_centavo_no_es_falso_positivo(self):
        """Tolerancia de redondeo entre Decimal local y numero JSON del cloud."""
        local = {'cxc': {'2026-08-18': {'count': 1, 'saldo': '100.00'}}}
        cloud = {'cxc': {'2026-08-18': {'count': 1, 'saldo': '100.005'}}}
        self.assertEqual(comparar_resumenes(local, cloud), [])

    def test_conteo_correcto_pero_suma_distinta(self):
        local = {'ventas': {'2026-08-18': {'count': 2, 'suma': '350.00', 'anuladas': 0}}}
        cloud = {'ventas': {'2026-08-18': {'count': 2, 'suma': '300.00', 'anuladas': 0}}}
        divergencias = comparar_resumenes(local, cloud)
        self.assertEqual(len(divergencias), 1)
        self.assertEqual(divergencias[0]['campo'], 'suma')
