from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.clientes.models import Cliente
from apps.cuentas_por_cobrar.models import MetodoPlazoCredito
from apps.inventario.models import Compra, DetalleCompra
from apps.productos.models import Categoria, Producto
from apps.permisos.testing import habilitar_cajero
from apps.ventas.services import procesar_venta_service


class ExportEstadoCuentaTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.sysadmin = User.objects.create_user(
            username='sysadmin_export',
            email='sysadmin_export@test.local',
            password='pass',
            rol='SYSADMIN',
            activo=True,
        )
        self.cajera = User.objects.create_user(
            username='cajera_export',
            email='cajera_export@test.local',
            password='pass',
            rol='CAJERA',
            activo=True,
        )
        # La venta exige `ventas.crear` server-side (RBAC del catalogo).
        habilitar_cajero(self.cajera)
        categoria = Categoria.objects.create(nombre='Export Test')
        self.producto = Producto.objects.create(
            sku='EXP-PROD-001',
            codigo_barras='EXP-PROD-001',
            nombre='Producto export',
            descripcion='',
            categoria=categoria,
            precio_venta=Decimal('100.00'),
            stock_minimo=1,
            activo=True,
            estado='nuevo',
            marca='',
            atributos={},
        )
        compra = Compra.objects.create(
            usuario=self.sysadmin,
            proveedor='Proveedor Export',
            numero_factura='FAC-EXP-001',
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
            nombre='Cliente Export',
            cedula_rnc='131999004',
            limite_credito=Decimal('1000.00'),
            activo=True,
        )
        self.metodo = MetodoPlazoCredito.objects.create(
            nombre='3 cuotas export test',
            tipo=MetodoPlazoCredito.TIPO_CUOTAS,
            dias_vencimiento=15,
            cantidad_cuotas=3,
            frecuencia=MetodoPlazoCredito.FRECUENCIA_MENSUAL,
            activo=True,
        )
        procesar_venta_service(
            usuario=self.cajera,
            datos={
                'carrito': [
                    {'id': self.producto.id, 'cantidad': 1, 'precio_venta': '100.00', 'descuento': '0.00'}
                ],
                'metodo_pago': 'credito',
                'cliente_id': self.cliente.id,
                'total': '100.00',
                'tipo_ecf': '31',
                'credito': {
                    'metodo_plazo_id': self.metodo.id,
                    'fecha_primer_vencimiento': '2026-07-15',
                    'monto_inicial': '10.00',
                    'metodo_inicial': 'efectivo',
                    'cantidad_cuotas': 3,
                    'interes_porcentaje': '10',
                },
            },
        )

    def test_pdf_devuelve_attachment(self):
        self.client.force_login(self.sysadmin)
        res = self.client.get(f'/cuentas-por-cobrar/cliente/{self.cliente.id}/pdf/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertIn('attachment', res['Content-Disposition'])
        self.assertTrue(res.content.startswith(b'%PDF'))

    def test_excel_devuelve_attachment(self):
        self.client.force_login(self.sysadmin)
        res = self.client.get(f'/cuentas-por-cobrar/cliente/{self.cliente.id}/excel/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            res['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        self.assertIn('attachment', res['Content-Disposition'])
        # XLSX es un zip: comienza con PK
        self.assertTrue(res.content.startswith(b'PK'))

    def test_export_requiere_permiso_ver(self):
        # CAJERA sin roles: requiere_permiso_local redirige (302), no entrega el archivo
        self.client.force_login(self.cajera)
        res = self.client.get(f'/cuentas-por-cobrar/cliente/{self.cliente.id}/pdf/')
        self.assertEqual(res.status_code, 302)
