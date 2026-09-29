from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from apps.caja.ingresos import ingresos_del_dia
from apps.caja.models import Caja, MovimientoCaja, TurnoCaja
from apps.caja.views import _desglose_serializable
from apps.clientes.models import Cliente
from apps.cuentas_por_cobrar.models import CuentaPorCobrar, MetodoPlazoCredito, PagoCxC
from apps.permisos.alcance import Alcance
from apps.reportes.conciliacion import generar_conciliacion
from apps.reportes.tests.test_auditoria_reportes import ReportesTestCase
from apps.ventas.models import Pago, Venta


class ConciliacionTests(ReportesTestCase):
    def setUp(self):
        super().setUp()
        self.hoy = timezone.localdate()
        self.caja = Caja.objects.create(nombre='Caja período', sucursal=self.sucursal_a)
        self.turno = TurnoCaja.objects.create(caja=self.caja, usuario=self.cajera, fondo_apertura=100)
        self.venta = self._venta(self.sucursal_a, '150.00')
        cliente = Cliente.objects.create(nombre='Cliente crédito')
        self.credito = Venta.objects.create(
            usuario=self.cajera, sucursal=self.sucursal_a, cliente=cliente,
            total=200, subtotal=200, condicion_pago='CREDITO', estado='COMPLETADA')
        Pago.objects.create(venta=self.credito, metodo='EFECTIVO', monto=50)
        Pago.objects.create(venta=self.credito, metodo='CREDITO', monto=150)
        cuenta = CuentaPorCobrar.objects.create(
            venta=self.credito, cliente=cliente, metodo_plazo=MetodoPlazoCredito.objects.create(nombre='30 días'),
            total=200, monto_inicial=50, saldo_original=150, saldo=110,
            fecha_limite=self.hoy + timedelta(days=30), creado_por=self.cajera, sucursal=self.sucursal_a)
        self.cobro = PagoCxC.objects.create(cuenta=cuenta, registrado_por=self.cajera, metodo='TARJETA', monto=40)
        for tipo, monto in [('INGRESO', 30), ('GASTO', 5), ('RETIRO', 10)]:
            MovimientoCaja.objects.create(turno=self.turno, tipo=tipo, monto=monto,
                                          descripcion=tipo, registrado_por=self.cajera)

    def reporte(self, inicio=None, fin=None):
        return generar_conciliacion(inicio or self.hoy, fin or self.hoy, Alcance({self.sucursal_a.pk}, False))

    def test_inicial_credito_cobros_y_movimientos_sin_duplicar(self):
        totales = self.reporte()['totales']
        esperado = {'ventas': '350', 'credito': '150', 'cobros_ventas': '200',
                    'cobros_cxc': '40', 'ingresos': '240', 'reposiciones': '30',
                    'gastos': '5', 'retiros': '10', 'neto': '255'}
        for campo, monto in esperado.items():
            self.assertEqual(totales[campo], Decimal(monto), campo)
        metodos = self.reporte()['metodos']
        self.assertEqual(sum(m['total'] for m in metodos), Decimal('240'))

    def test_excluye_anulaciones_y_otra_sucursal(self):
        self._venta(self.sucursal_b, '900')
        self.venta.estado = 'ANULADA'
        self.venta.save()
        self.cobro.estado = 'ANULADO'
        self.cobro.save()
        self.assertEqual(self.reporte()['totales']['ingresos'], Decimal('50'))

    def test_cobro_de_venta_anterior_y_dias_vacios(self):
        ayer = self.hoy - timedelta(days=1)
        Venta.objects.filter(pk=self.credito.pk).update(fecha_venta=timezone.make_aware(datetime.combine(ayer, time(12))))
        reporte = self.reporte(inicio=self.hoy - timedelta(days=2))
        self.assertEqual(reporte['dias'][0]['ingresos'], 0)
        self.assertEqual(reporte['dias'][1]['ingresos'], 50)
        self.assertEqual(reporte['dias'][2]['ingresos'], 190)

    def test_limites_de_dia_local_inclusivos(self):
        inicio = timezone.make_aware(datetime.combine(self.hoy, time.min))
        Venta.objects.filter(pk=self.venta.pk).update(fecha_venta=inicio - timedelta(microseconds=1))
        self.assertEqual(self.reporte()['totales']['ventas'], 200)
        Venta.objects.filter(pk=self.venta.pk).update(fecha_venta=inicio)
        self.assertEqual(self.reporte()['totales']['ventas'], 350)
        Venta.objects.filter(pk=self.venta.pk).update(fecha_venta=inicio + timedelta(days=1))
        self.assertEqual(self.reporte()['totales']['ventas'], 200)

    def test_arqueos_guardados_por_fecha_cierre_no_recalculados(self):
        TurnoCaja.objects.filter(pk=self.turno.pk).update(
            fecha_apertura=timezone.now() - timedelta(days=1), fecha_cierre=timezone.now(),
            estado='CERRADO', monto_esperado=999, monto_contado=990, diferencia=-9)
        reporte = self.reporte()
        self.assertEqual(reporte['totales']['esperado'], 999)
        self.assertEqual(reporte['totales']['diferencia'], -9)
        self.assertEqual(reporte['totales']['cierres'], 1)
        self.assertEqual(reporte['turnos_abiertos'], 0)

    def test_ingresos_dia_cajero_y_conteo_ciego(self):
        self._venta(self.sucursal_a, '700', usuario=self.admin)
        self._venta(self.sucursal_b, '900')
        self.assertEqual(ingresos_del_dia(self.turno)['total'], '240.00')
        oculto = _desglose_serializable(self.turno.resumen_operativo(), True, self.turno)
        self.assertNotIn('ingresos_dia', oculto)
        visible = _desglose_serializable(self.turno.resumen_operativo(), False, self.turno)
        self.assertEqual(visible['ingresos_dia']['total'], '240.00')

    def test_html_pdf_mismo_alcance_y_totales(self):
        supervisor = self._supervisor_de(self.sucursal_a)
        self._venta(self.sucursal_b, '900')
        self.client.force_login(supervisor)
        params = {'inicio': self.hoy.isoformat(), 'fin': self.hoy.isoformat()}
        html = self.client.get(reverse('reportes:conciliacion'), params)
        self.assertEqual(html.status_code, 200)
        self.assertEqual(html.context['reporte']['totales']['ingresos'], Decimal('240'))
        pdf = self.client.get(reverse('reportes:conciliacion'), params | {'formato': 'pdf'})
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        self.assertIn('no-store', pdf['Cache-Control'])

    def test_sucursal_ajena_rechazada_html_y_pdf(self):
        self.client.force_login(self._supervisor_de(self.sucursal_a))
        for formato in ('html', 'pdf'):
            response = self.client.get(reverse('reportes:conciliacion'), {
                'inicio': self.hoy, 'fin': self.hoy, 'sucursal': self.sucursal_b.pk, 'formato': formato})
            self.assertEqual(response.status_code, 400)

    def test_seleccion_explicita_no_incluye_legacy(self):
        self._venta(None, '999')
        self.client.force_login(self._supervisor_de(self.sucursal_a))
        response = self.client.get(reverse('reportes:conciliacion'), {
            'inicio': self.hoy, 'fin': self.hoy, 'sucursal': self.sucursal_a.pk})
        self.assertEqual(response.context['reporte']['totales']['ingresos'], 240)

    def test_validacion_fechas(self):
        self.client.force_login(self._supervisor_de(self.sucursal_a))
        for inicio, fin in [('mal', self.hoy), (self.hoy, 'mal'),
                            (self.hoy, self.hoy - timedelta(days=1)),
                            (self.hoy, self.hoy + timedelta(days=1)),
                            (self.hoy - timedelta(days=366), self.hoy)]:
            response = self.client.get(reverse('reportes:conciliacion'), {'inicio': inicio, 'fin': fin})
            self.assertEqual(response.status_code, 400)

    def test_sin_permiso_no_consulta_reporte(self):
        self.client.force_login(self.cajera)
        with patch('apps.reportes.views_conciliacion.generar_conciliacion') as generar:
            response = self.client.get(reverse('reportes:conciliacion'))
            self.assertEqual(response.status_code, 403)
            generar.assert_not_called()

    def test_pdf_vacio_y_multipagina(self):
        from apps.reportes.conciliacion import generar_pdf
        vacio = generar_conciliacion(self.hoy - timedelta(days=40), self.hoy - timedelta(days=1), Alcance(set(), False))
        contenido = generar_pdf(vacio, self.sucursal_a, 'Sucursal A')
        self.assertTrue(contenido.startswith(b'%PDF'))
        self.assertGreater(contenido.count(b'/Type /Page'), 2)
