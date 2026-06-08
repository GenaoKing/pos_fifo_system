from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.configuracion.models import AccesoRapidoPOS
from apps.productos.models import Categoria, Producto


class AccesosRapidosPOSTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.usuario = User.objects.create_user(
            username='cajera_rapidos',
            email='cajera_rapidos@example.com',
            password='pass',
            rol='CAJERA',
            activo=True,
        )
        self.client.force_login(self.usuario)

        self.categoria = Categoria.objects.create(nombre='Vasos')
        self.otra_categoria = Categoria.objects.create(nombre='Fundas')
        self.producto = Producto.objects.create(
            sku='RAP-001',
            codigo_barras='RAP-001',
            nombre='Vaso rapido',
            descripcion='',
            categoria=self.categoria,
            precio_venta='100.00',
            stock_minimo=5,
            activo=True,
            estado='nuevo',
            marca='',
            atributos={},
        )
        self.producto_inactivo = Producto.objects.create(
            sku='RAP-002',
            codigo_barras='RAP-002',
            nombre='Producto inactivo',
            descripcion='',
            categoria=self.categoria,
            precio_venta='50.00',
            stock_minimo=5,
            activo=False,
            estado='nuevo',
            marca='',
            atributos={},
        )

    def test_lista_accesos_rapidos_activos_y_validos(self):
        AccesoRapidoPOS.objects.create(
            etiqueta='Vaso',
            tipo=AccesoRapidoPOS.TIPO_PRODUCTO,
            producto=self.producto,
            orden=2,
            color='verde',
        )
        AccesoRapidoPOS.objects.create(
            etiqueta='Fundas',
            tipo=AccesoRapidoPOS.TIPO_CATEGORIA,
            categoria=self.otra_categoria,
            orden=1,
            color='ambar',
        )
        AccesoRapidoPOS.objects.create(
            etiqueta='Inactivo',
            tipo=AccesoRapidoPOS.TIPO_PRODUCTO,
            producto=self.producto_inactivo,
            orden=3,
        )

        response = self.client.get('/pos/api/accesos-rapidos/')

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual([a['etiqueta'] for a in data['accesos']], ['Fundas', 'Vaso'])
        self.assertEqual(data['accesos'][0]['tipo'], 'categoria')
        self.assertEqual(data['accesos'][1]['producto_id'], self.producto.id)

    def test_producto_por_id_devuelve_precio_fresco(self):
        self.producto.precio_venta = '125.00'
        self.producto.save(update_fields=['precio_venta'])

        response = self.client.get(f'/pos/api/producto-id/{self.producto.id}/')

        self.assertEqual(response.status_code, 200)
        producto = response.json()['producto']
        self.assertEqual(producto['precio_venta'], 125.0)
        self.assertEqual(producto['precio_formateado'], '$125.00')

    def test_busqueda_por_nombre_de_categoria(self):
        response = self.client.get('/pos/api/buscar/', {'q': 'Vasos'})

        self.assertEqual(response.status_code, 200)
        productos = response.json()['productos']
        self.assertEqual(len(productos), 1)
        self.assertEqual(productos[0]['id'], self.producto.id)
