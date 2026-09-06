"""apps/caja/tests/test_cuadre.py

Cubre lo agregado para el cierre de caja mejorado:
- `_desglose_serializable`: desglose por metodo (incl. credito) y el enmascarado
  del conteo ciego.
- Vista `cuadre_ticket`: render, formato y control de acceso (dueno/admin).
- `api_estado_turno` enriquecido + conteo ciego.
- `api_imprimir_cuadre_termica`: modulo apagado y acceso.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.caja import views as caja_views
from apps.caja.models import Caja, MovimientoCaja, TurnoCaja
from apps.clientes.models import Cliente
from apps.configuracion.models import ConfiguracionNegocio
from apps.cuentas_por_cobrar.models import (
    CuentaPorCobrar, MetodoPlazoCredito, PagoCxC,
)
from apps.negocios.models import Negocio
from apps.permisos import testing as permisos_testing
from apps.sucursales.models import Sucursal
from apps.ventas.models import Pago, Venta

User = get_user_model()


class _CuadreBase(TestCase):
    def setUp(self):
        cache.clear()
        self.negocio = Negocio.objects.create(nombre='Cuadre', slug='cuadre')
        self.sucursal = Sucursal.objects.create(
            negocio=self.negocio, codigo='CUA-1', nombre='Cuadre 1',
        )
        # Una unica ConfiguracionNegocio -> get_config() la resuelve sin ambiguedad.
        self.config = ConfiguracionNegocio.objects.create(nombre_negocio='Tienda Cuadre')

        self.cajera = User.objects.create_user(
            username='cajera_cuadre', email='cajera_cuadre@test.local',
            password='pass', activo=True,
        )
        permisos_testing.habilitar_cajero(
            self.cajera, permisos=['ventas.crear', 'caja.operar'],
        )

        # Caja sin sucursal (legacy) -> siempre en alcance; el control real es la
        # pertenencia del turno.
        self.caja = Caja.objects.create(nombre='Caja Cuadre', activa=True)
        self.turno = TurnoCaja.objects.create(
            caja=self.caja, usuario=self.cajera, fondo_apertura=Decimal('100.00'),
        )

    def tearDown(self):
        cache.clear()

    def _poblar_turno(self):
        """Venta contado mixta (efectivo+tarjeta) + venta credito + cobro CxC + retiro."""
        contado = Venta.objects.create(
            numero_venta='CUA-V1', fecha_venta=timezone.now(), usuario=self.cajera,
            sucursal=self.sucursal, subtotal=Decimal('150.00'),
            total=Decimal('150.00'), condicion_pago='CONTADO',
        )
        Pago.objects.create(venta=contado, metodo='EFECTIVO', monto=Decimal('100.00'), turno_caja=self.turno)
        Pago.objects.create(venta=contado, metodo='TARJETA', monto=Decimal('50.00'), turno_caja=self.turno)

        cliente = Cliente.objects.create(nombre='Cliente cuadre')
        credito = Venta.objects.create(
            numero_venta='CUA-V2', fecha_venta=timezone.now(), usuario=self.cajera,
            sucursal=self.sucursal, cliente=cliente, subtotal=Decimal('200.00'),
            total=Decimal('200.00'), condicion_pago='CREDITO',
        )
        Pago.objects.create(venta=credito, metodo='CREDITO', monto=Decimal('200.00'), turno_caja=self.turno)
        plazo = MetodoPlazoCredito.objects.create(nombre='30d cuadre')
        cuenta = CuentaPorCobrar.objects.create(
            cliente=cliente, venta=credito, metodo_plazo=plazo, total=Decimal('200.00'),
            saldo_original=Decimal('200.00'), saldo=Decimal('200.00'),
            fecha_limite=timezone.localdate() + timedelta(days=30),
            creado_por=self.cajera, sucursal=self.sucursal,
        )
        PagoCxC.objects.create(
            cuenta=cuenta, metodo='EFECTIVO', monto=Decimal('40.00'),
            registrado_por=self.cajera, turno_caja=self.turno,
        )
        MovimientoCaja.objects.create(
            turno=self.turno, tipo='RETIRO', monto=Decimal('10.00'),
            descripcion='deposito', registrado_por=self.cajera,
        )


class DesgloseSerializableTests(_CuadreBase):
    def test_incluye_metodos_totales_y_esperado(self):
        self._poblar_turno()
        data = caja_views._desglose_serializable(self.turno.resumen_operativo())

        self.assertEqual(data['pagos_por_metodo']['EFECTIVO'], '100.00')
        self.assertEqual(data['pagos_por_metodo']['TARJETA'], '50.00')
        self.assertEqual(data['pagos_por_metodo']['CREDITO'], '200.00')
        self.assertEqual(data['cobros_cxc_por_metodo']['EFECTIVO'], '40.00')
        self.assertEqual(data['cobros_cxc_total'], '40.00')
        self.assertEqual(data['total_ventas'], '350.00')
        self.assertEqual(data['cantidad_ventas'], 2)
        # 100 fondo + 100 efec ventas + 40 cobro efec - 10 retiro = 230
        self.assertEqual(data['esperado'], '230.00')
        self.assertFalse(data['ocultar_efectivo'])

    def test_conteo_ciego_enmascara_efectivo(self):
        self._poblar_turno()
        data = caja_views._desglose_serializable(
            self.turno.resumen_operativo(), ocultar_efectivo=True,
        )
        self.assertTrue(data['ocultar_efectivo'])
        for clave in ('esperado', 'fondo_apertura', 'efectivo_ventas',
                      'efectivo_cxc', 'retiros', 'gastos', 'ingresos',
                      'cobros_cxc_total'):
            self.assertIsNone(data[clave], clave)
        # El efectivo se saca de los desgloses por metodo; el resto permanece.
        self.assertNotIn('EFECTIVO', data['pagos_por_metodo'])
        self.assertNotIn('EFECTIVO', data['cobros_cxc_por_metodo'])
        self.assertEqual(data['pagos_por_metodo']['CREDITO'], '200.00')
        self.assertEqual(data['total_ventas'], '350.00')


class CuadreTicketViewTests(_CuadreBase):
    def test_dueno_ve_el_cuadre(self):
        self._poblar_turno()
        self.turno.cerrar(Decimal('230.00'), self.cajera)
        self.client.force_login(self.cajera)
        resp = self.client.get(reverse('caja:cuadre_ticket', args=[self.turno.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Cuadre de Caja')
        self.assertContains(resp, 'Tienda Cuadre')   # header de negocio
        self.assertContains(resp, '200.00')          # credito vendido en el desglose

    def test_formato_ticket_marca_el_body(self):
        self.client.force_login(self.cajera)
        url = reverse('caja:cuadre_ticket', args=[self.turno.id]) + '?formato=ticket'
        self.assertContains(self.client.get(url), 'formato-ticket')

    def test_cajero_ajeno_no_accede(self):
        otra = User.objects.create_user(
            username='otra_cuadre', email='otra_cuadre@test.local',
            password='pass', activo=True,
        )
        permisos_testing.habilitar_cajero(otra, permisos=['caja.operar'])
        self.client.force_login(otra)
        resp = self.client.get(reverse('caja:cuadre_ticket', args=[self.turno.id]))
        self.assertEqual(resp.status_code, 302)  # redirigido, no ve el turno ajeno


class EstadoTurnoEnriquecidoTests(_CuadreBase):
    def test_estado_incluye_desglose_por_metodo(self):
        self._poblar_turno()
        self.client.force_login(self.cajera)
        data = self.client.get(reverse('caja:api_estado')).json()
        self.assertTrue(data['tiene_turno'])
        self.assertEqual(data['desglose']['pagos_por_metodo']['CREDITO'], '200.00')
        self.assertIn('cobros_cxc_por_metodo', data['desglose'])
        self.assertFalse(data['desglose']['ocultar_efectivo'])

    def test_conteo_ciego_oculta_esperado_a_la_cajera(self):
        self.config.conteo_ciego_caja = True
        self.config.save()
        cache.clear()
        self._poblar_turno()
        self.client.force_login(self.cajera)
        data = self.client.get(reverse('caja:api_estado')).json()
        self.assertTrue(data['desglose']['ocultar_efectivo'])
        self.assertIsNone(data['desglose']['esperado'])


class ImprimirTermicaTests(_CuadreBase):
    def test_modulo_apagado_no_imprime(self):
        self.config.modulo_impresion_termica = False
        self.config.save()
        cache.clear()
        self.turno.cerrar(Decimal('100.00'), self.cajera)
        self.client.force_login(self.cajera)
        resp = self.client.post(reverse('caja:api_cuadre_termica', args=[self.turno.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['success'])

    def test_cajero_ajeno_403(self):
        otra = User.objects.create_user(
            username='otra_term', email='otra_term@test.local',
            password='pass', activo=True,
        )
        permisos_testing.habilitar_cajero(otra, permisos=['caja.operar'])
        self.client.force_login(otra)
        resp = self.client.post(reverse('caja:api_cuadre_termica', args=[self.turno.id]))
        self.assertEqual(resp.status_code, 403)
