import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from apps.configuracion.models import AccesoRapidoPOS
from apps.permisos import testing as permisos_testing
from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal
from apps.sync.models import MutacionMaestro


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


class AccesosRapidosPorSucursalTests(TestCase):
    """CFG-010 pata 2 — el endpoint acota los accesos a la sucursal actual.

    La reproduccion del hallazgo: un acceso creado en la sucursal A aparecia al
    consultar el POS como sucursal B. Ahora cada sucursal ve SOLO los suyos mas
    los legacy sin sucursal (NULL = global), y `get_sucursal_actual()` decide
    cual es la actual por `SUCURSAL_CODIGO`.
    """

    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.usuario = User.objects.create_user(
            username='cajera_scope', email='cajera_scope@example.com',
            password='pass', rol='CAJERA', activo=True,
        )
        self.client.force_login(self.usuario)

        self.negocio = permisos_testing.crear_negocio('Negocio Scope')
        self.suc_a = Sucursal.objects.create(
            codigo='SCOPE-A', nombre='Tienda A', activa=True, negocio=self.negocio,
        )
        self.suc_b = Sucursal.objects.create(
            codigo='SCOPE-B', nombre='Tienda B', activa=True, negocio=self.negocio,
        )

        self.categoria = Categoria.objects.create(nombre='Vasos')
        self.producto = Producto.objects.create(
            sku='SCOPE-1', codigo_barras='SCOPE-1', nombre='Vaso', descripcion='',
            categoria=self.categoria, precio_venta='100.00', stock_minimo=5,
            activo=True, estado='nuevo', marca='', atributos={},
        )

        # Mismo producto vendible en los tres: lo que diferencia es la sucursal.
        AccesoRapidoPOS.objects.create(
            etiqueta='SoloA', tipo=AccesoRapidoPOS.TIPO_PRODUCTO,
            producto=self.producto, sucursal=self.suc_a,
        )
        AccesoRapidoPOS.objects.create(
            etiqueta='SoloB', tipo=AccesoRapidoPOS.TIPO_PRODUCTO,
            producto=self.producto, sucursal=self.suc_b,
        )
        AccesoRapidoPOS.objects.create(
            etiqueta='Legacy', tipo=AccesoRapidoPOS.TIPO_PRODUCTO,
            producto=self.producto,  # sucursal=None
        )

    def tearDown(self):
        cache.clear()

    def _etiquetas_como(self, codigo):
        with self.settings(SUCURSAL_CODIGO=codigo):
            cache.clear()
            response = self.client.get('/pos/api/accesos-rapidos/')
        self.assertEqual(response.status_code, 200)
        return {a['etiqueta'] for a in response.json()['accesos']}

    def test_cada_sucursal_ve_los_suyos_mas_los_legacy(self):
        self.assertEqual(self._etiquetas_como('SCOPE-A'), {'SoloA', 'Legacy'})

    def test_la_otra_sucursal_no_ve_los_de_la_primera(self):
        self.assertEqual(self._etiquetas_como('SCOPE-B'), {'SoloB', 'Legacy'})


class AccesosRapidosConflictoMaestroTests(TestCase):
    """PRO-007 / CT-04 — el acceso rapido de categoria tambien respeta un
    conflicto de maestro, no solo `activa`.

    La rama de producto ya usaba `es_vendible` (que incluye el conflicto);
    la de categoria solo miraba `activa`. No era explotable -- el boton solo
    dispara `buscar_productos`, que ya filtra con `productos_vendibles()` --
    pero mostraba un boton "vivo" para algo que en el fondo no vendia nada.
    """

    def setUp(self):
        User = get_user_model()
        self.usuario = User.objects.create_user(
            username='cajera_conflicto', email='cajera_conflicto@example.com',
            password='pass', rol='CAJERA', activo=True,
        )
        self.client.force_login(self.usuario)

        self.negocio = permisos_testing.crear_negocio('Negocio Conflicto')
        self.sucursal = Sucursal.objects.create(
            codigo='CONF-01', nombre='Tienda conflicto', activa=True,
            negocio=self.negocio,
        )
        self.categoria = Categoria.objects.create(nombre='Vasos', activa=True)
        self.producto = Producto.objects.create(
            sku='CONF-1', codigo_barras='CONF-1', nombre='Vaso', descripcion='',
            categoria=self.categoria, precio_venta='100.00', stock_minimo=5,
            activo=True, estado='nuevo', marca='', atributos={},
        )
        AccesoRapidoPOS.objects.create(
            etiqueta='Vasos', tipo=AccesoRapidoPOS.TIPO_CATEGORIA,
            categoria=self.categoria,
        )

    def test_categoria_activa_sin_conflicto_aparece(self):
        response = self.client.get('/pos/api/accesos-rapidos/')
        self.assertEqual({a['etiqueta'] for a in response.json()['accesos']}, {'Vasos'})

    def test_categoria_con_conflicto_de_maestro_no_aparece(self):
        mutacion = MutacionMaestro.objects.create(
            mutacion_id=uuid.uuid4(),
            entidad=MutacionMaestro.Entidad.CATEGORIA,
            entidad_id=self.categoria.id,
            operacion=MutacionMaestro.Operacion.ACTUALIZAR,
            sucursal=self.sucursal,
            sucursal_codigo=self.sucursal.codigo,
        )
        mutacion.marcar_conflicto('CAS_REVISION_MISMATCH')

        response = self.client.get('/pos/api/accesos-rapidos/')

        self.assertEqual(response.json()['accesos'], [])
