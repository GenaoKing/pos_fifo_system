from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.clientes.models import Cliente
from apps.configuracion.models import ConfiguracionNegocio
from apps.cotizaciones.models import Cotizacion, DetalleCotizacion
from apps.productos.models import Categoria, Producto


class CotizacionPdfTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='admin_cot_pdf',
            email='admin_cot_pdf@test.local',
            password='pass',
            rol='ADMIN',
            activo=True,
        )
        ConfiguracionNegocio.objects.create(
            nombre_negocio='Royal PDF',
            modulo_cotizaciones=True,
        )
        cliente = Cliente.objects.create(
            tipo='PERSONAL',
            nombre='Cliente PDF',
            cedula_rnc='00100000001',
            activo=True,
        )
        categoria = Categoria.objects.create(nombre='PDF Cot')
        producto = Producto.objects.create(
            sku='PDF-COT-001',
            codigo_barras='PDF-COT-001',
            nombre='Producto PDF con nombre largo para envolver texto',
            descripcion='',
            categoria=categoria,
            precio_venta=Decimal('120.00'),
            stock_minimo=1,
            activo=True,
            estado='nuevo',
            marca='',
            atributos={},
        )
        self.cotizacion = Cotizacion.objects.create(
            cliente=cliente,
            usuario=self.user,
            notas='Notas comerciales de prueba para el PDF.',
        )
        DetalleCotizacion.objects.create(
            cotizacion=self.cotizacion,
            producto=producto,
            cantidad=2,
            precio_unitario=Decimal('120.00'),
            descuento_monto=Decimal('10.00'),
            subtotal=Decimal('0.00'),
            total_linea=Decimal('0.00'),
        )
        self.cotizacion.calcular_totales()
        self.cotizacion.save()

    def test_cotizacion_pdf_descarga_attachment(self):
        self.client.force_login(self.user)
        res = self.client.get(f'/cotizaciones/{self.cotizacion.id}/pdf/')

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertIn('attachment', res['Content-Disposition'])
        self.assertTrue(res.content.startswith(b'%PDF'))
