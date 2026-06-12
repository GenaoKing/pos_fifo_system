from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.clientes.models import Cliente
from apps.configuracion.models import ConfiguracionNegocio
from apps.productos.models import Categoria, Producto
from apps.ventas.models import DetalleVenta, FinanciacionCooperativa, Pago, Venta


class FinanciacionPdfTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='admin_fin_pdf',
            email='admin_fin_pdf@test.local',
            password='pass',
            rol='ADMIN',
            activo=True,
        )
        ConfiguracionNegocio.objects.create(
            nombre_negocio='Royal PDF',
            modulo_financiacion_coop=True,
        )
        cliente = Cliente.objects.create(
            tipo='PERSONAL',
            nombre='Cliente Fin PDF',
            cedula_rnc='00200000002',
            activo=True,
        )
        categoria = Categoria.objects.create(nombre='PDF Fin')
        producto = Producto.objects.create(
            sku='PDF-FIN-001',
            codigo_barras='PDF-FIN-001',
            nombre='Producto financiado PDF',
            descripcion='',
            categoria=categoria,
            precio_venta=Decimal('200.00'),
            stock_minimo=1,
            activo=True,
            estado='nuevo',
            marca='',
            atributos={},
        )
        self.venta = Venta.objects.create(
            usuario=self.user,
            cliente=cliente,
            subtotal=Decimal('200.00'),
            descuento_total=Decimal('15.00'),
            total=Decimal('185.00'),
        )
        DetalleVenta.objects.create(
            venta=self.venta,
            producto=producto,
            cantidad=1,
            precio_unitario=Decimal('200.00'),
            descuento_monto=Decimal('15.00'),
            subtotal=Decimal('0.00'),
            total_linea=Decimal('0.00'),
        )
        Pago.objects.create(
            venta=self.venta,
            metodo='TRANSFERENCIA',
            monto=Decimal('185.00'),
        )
        FinanciacionCooperativa.objects.create(
            venta=self.venta,
            nombre_cliente='Cliente Fin PDF',
            cedula_cliente='00200000002',
            telefono_cliente='809-555-0000',
            direccion_cliente='Santo Domingo',
            nombre_cooperativa='Cooperativa PDF',
            codigo_aprobacion='APR-001',
            usuario=self.user,
        )

    def test_financiacion_pdf_responde_inline(self):
        self.client.force_login(self.user)
        res = self.client.get(f'/pos/financiacion/{self.venta.id}/pdf/')

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertIn('inline', res['Content-Disposition'])
        self.assertTrue(res.content.startswith(b'%PDF'))
