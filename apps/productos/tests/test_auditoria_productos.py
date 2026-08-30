"""
apps/productos/tests/test_auditoria_productos.py

Regresion de los hallazgos de `docs/exploracion/AUDITORIA_CODIGO_APPS_PRODUCTOS.md`.

La app no aportaba casos propios (PRO-018); este modulo es el arranque.
"""
import io
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.permisos import testing as permisos_testing
from apps.productos.models import Categoria, Producto, productos_vendibles
from apps.ventas.services import procesar_venta_service
from apps.ventas.services.exceptions import ProductoInexistenteError

User = get_user_model()


def _png_valido(color=(255, 0, 0)):
    """Un PNG real, generado en memoria."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', (12, 12), color).save(buffer, format='PNG')
    return buffer.getvalue()


class ProductosTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.categoria = Categoria.objects.create(nombre='Herramientas', activa=True)
        self.producto = Producto.objects.create(
            sku='PRO-001', codigo_barras='PRO-001',
            nombre='Martillo', descripcion='', categoria=self.categoria,
            precio_venta=Decimal('500.00'), stock_minimo=1, activo=True,
            estado='nuevo', marca='Truper', atributos={},
        )

    def tearDown(self):
        cache.clear()

    def _usuario(self, username, permisos=(), rol='CAJERA'):
        user = User.objects.create_user(
            username=username, email=f'{username}@test.local',
            password='Prueba123', rol=rol, activo=True,
        )
        permisos_testing.habilitar_cajero(user, permisos=list(permisos))
        return user

    def _post(self, user, nombre, args=None, cuerpo=None, **extra):
        self.client.force_login(user)
        url = reverse(nombre, args=args or [])
        if cuerpo is None:
            return self.client.post(url, **extra)
        return self.client.post(
            url, data=json.dumps(cuerpo),
            content_type='application/json', **extra,
        )


class GatesDelCatalogoTests(ProductosTestCase):
    """PRO-001: el CRUD HTML aplica el catalogo RBAC."""

    def setUp(self):
        super().setUp()
        self.cajera = self._usuario('cajera_pro', permisos=['ventas.crear'])

    def test_sin_permiso_no_se_crea_un_producto(self):
        """
        La reproduccion: una cajera sin permisos del modulo creo una categoria y
        un producto, edito el precio a 1.00 y desactivo ambos.
        """
        respuesta = self._post(self.cajera, 'productos:crear', cuerpo={
            'nombre': 'Colado', 'sku': 'X-1', 'precio_venta': '1.00',
            'categoria_id': self.categoria.id,
        })

        self.assertEqual(respuesta.status_code, 403)
        self.assertFalse(Producto.objects.filter(sku='X-1').exists())

    def test_sin_permiso_no_se_edita_el_precio(self):
        respuesta = self._post(
            self.cajera, 'productos:editar', args=[self.producto.id], cuerpo={
                'nombre': 'Martillo', 'precio_venta': '1.00',
                'categoria_id': self.categoria.id,
            },
        )

        self.assertEqual(respuesta.status_code, 403)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.precio_venta, Decimal('500.00'))

    def test_sin_permiso_no_se_desactiva(self):
        respuesta = self._post(
            self.cajera, 'productos:toggle_estado', args=[self.producto.id],
        )

        self.assertEqual(respuesta.status_code, 403)
        self.producto.refresh_from_db()
        self.assertTrue(self.producto.activo)

    def test_sin_permiso_no_se_crea_una_categoria(self):
        respuesta = self._post(self.cajera, 'productos:crear_categoria', cuerpo={
            'nombre': 'Categoria Colada',
        })

        self.assertEqual(respuesta.status_code, 403)
        self.assertFalse(Categoria.objects.filter(nombre='Categoria Colada').exists())

    def test_sin_permiso_no_se_desactiva_una_categoria(self):
        respuesta = self._post(
            self.cajera, 'productos:toggle_estado_categoria',
            args=[self.categoria.id],
        )

        self.assertEqual(respuesta.status_code, 403)

    def test_sin_permiso_no_se_lista_el_catalogo(self):
        self.client.force_login(self.cajera)

        respuesta = self.client.get(reverse('productos:lista'))

        self.assertEqual(respuesta.status_code, 302)

    def test_con_permiso_si_se_crea(self):
        autorizado = self._usuario(
            'con_crear', permisos=['productos.ver', 'productos.crear'],
        )

        respuesta = self._post(autorizado, 'productos:crear', cuerpo={
            'nombre': 'Legitimo', 'precio_venta': '100.00',
            'categoria_id': self.categoria.id,
        })

        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertTrue(Producto.objects.filter(nombre='Legitimo').exists())


class SerializacionSeguraTests(ProductosTestCase):
    """PRO-005: XSS persistente por `|safe` dentro de <script>."""

    def test_un_nombre_hostil_no_cierra_el_bloque_script(self):
        """
        La reproduccion: un nombre con `</script><script>...` aparecia en la
        respuesta cerrando el bloque original y conservando el payload.
        """
        Producto.objects.create(
            sku='XSS-1', codigo_barras='XSS-1',
            nombre='</script><script>window.__xss=1</script>',
            descripcion='', categoria=self.categoria,
            precio_venta=Decimal('10.00'), stock_minimo=1, activo=True,
            estado='nuevo', marca='', atributos={},
        )
        admin = self._usuario('admin_xss', rol='ADMIN')
        self.client.force_login(admin)

        html = self.client.get(reverse('productos:lista')).content.decode()

        self.assertNotIn('<script>window.__xss=1</script>', html)
        self.assertIn('id="productos-data"', html)

    def test_una_categoria_hostil_tampoco(self):
        Categoria.objects.create(
            nombre='</script><script>window.__xss2=1</script>', activa=True,
        )
        admin = self._usuario('admin_xss2', rol='ADMIN')
        self.client.force_login(admin)

        html = self.client.get(reverse('productos:categorias')).content.decode()

        self.assertNotIn('<script>window.__xss2=1</script>', html)
        self.assertIn('id="categorias-data"', html)

    def test_las_plantillas_ya_no_usan_safe_para_los_datos(self):
        import pathlib

        from django.conf import settings

        raiz = pathlib.Path(settings.BASE_DIR) / 'templates' / 'productos'
        for nombre, variable in (
            ('lista_productos.html', 'productos_json'),
            ('lista_categorias.html', 'categorias_json'),
        ):
            with self.subTest(plantilla=nombre):
                fuente = (raiz / nombre).read_text(encoding='utf-8')
                self.assertNotIn(f'{{{{ {variable}|safe }}}}', fuente)
                self.assertIn(f'{variable}|json_script', fuente)


class SubidaDeImagenTests(ProductosTestCase):
    """PRO-006: la imagen se valida antes de publicarse."""

    def setUp(self):
        super().setUp()
        self.fotografo = self._usuario(
            'fotografo', permisos=['productos.ver', 'productos.fotografiar'],
        )
        self.client.force_login(self.fotografo)

    def _subir(self, contenido, nombre='foto.png', tipo='image/png'):
        return self.client.post(
            reverse('productos:subir_imagen', args=[self.producto.id]),
            {'imagen': SimpleUploadedFile(nombre, contenido, content_type=tipo)},
        )

    def test_un_html_disfrazado_de_imagen_se_rechaza(self):
        """
        La reproduccion: se cargaron bytes HTML con tipo `text/plain` como
        imagen de producto; la vista respondio exito y el archivo quedo
        guardado y servido desde media.
        """
        respuesta = self._subir(b'<html><body>hola</body></html>', 'x.png')

        self.assertEqual(respuesta.status_code, 400)
        self.producto.refresh_from_db()
        self.assertFalse(self.producto.imagen)

    def test_una_extension_mentirosa_no_alcanza(self):
        """El nombre lo pone el cliente; el contenido es lo que se mira."""
        respuesta = self._subir(b'no soy una imagen', 'foto.png', 'image/png')

        self.assertEqual(respuesta.status_code, 400)

    def test_un_archivo_enorme_se_rechaza_sin_decodificar(self):
        from utils.imagenes import TAMANO_MAX_BYTES

        gigante = b'\x00' * (TAMANO_MAX_BYTES + 1024)
        respuesta = self._subir(gigante, 'grande.png')

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('MB', respuesta.json()['message'])

    def test_un_png_real_si_se_acepta(self):
        respuesta = self._subir(_png_valido())

        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.producto.refresh_from_db()
        self.assertTrue(self.producto.imagen)

    def test_el_nombre_lo_pone_el_servidor(self):
        """Un nombre del cliente puede traer rutas o caracteres de control."""
        self._subir(_png_valido(), nombre='../../etc/passwd.png')

        self.producto.refresh_from_db()
        self.assertNotIn('passwd', self.producto.imagen.name)
        self.assertIn(f'producto-{self.producto.id}', self.producto.imagen.name)

    def test_sin_permiso_no_se_sube(self):
        pelado = self._usuario('sin_foto', permisos=['ventas.crear'])
        self.client.force_login(pelado)

        respuesta = self._subir(_png_valido())

        self.assertEqual(respuesta.status_code, 403)


class VendibilidadTests(ProductosTestCase):
    """PRO-007: una sola condicion, aplicada en la transaccion de venta."""

    def setUp(self):
        super().setUp()
        self.cajera = self._usuario('cajera_venta', permisos=['ventas.crear'])
        from apps.inventario.models import Compra, DetalleCompra

        compra = Compra.objects.create(
            usuario=self.cajera, proveedor='Prov', numero_factura='F-PRO-1',
            total=Decimal('1000.00'),
        )
        DetalleCompra.objects.create(
            compra=compra, producto=self.producto, cantidad=20,
            costo_unitario=Decimal('50.00'), subtotal=Decimal('1000.00'),
        )

    def _vender(self):
        return procesar_venta_service(
            usuario=self.cajera,
            datos={
                'carrito': [{
                    'id': self.producto.id, 'cantidad': 1,
                    'precio_venta': '500.00', 'descuento': '0.00',
                }],
                'metodo_pago': 'efectivo',
                'total': '500.00',
            },
        )

    def test_un_producto_de_categoria_inactiva_no_se_vende(self):
        """
        La reproduccion: el cargador transaccional recuperaba tanto un producto
        inactivo como uno cuya categoria estaba dada de baja. La baja
        administrativa no era una garantia del backend.
        """
        self.categoria.activa = False
        self.categoria.save(update_fields=['activa'])

        with self.assertRaises(ProductoInexistenteError) as ctx:
            self._vender()

        self.assertIn('categoria inactiva', str(ctx.exception))

    def test_un_producto_inactivo_no_se_vende(self):
        self.producto.activo = False
        self.producto.save(update_fields=['activo'])

        with self.assertRaises(ProductoInexistenteError) as ctx:
            self._vender()

        self.assertIn('inactivo', str(ctx.exception))

    def test_un_producto_vendible_si_se_vende(self):
        venta = self._vender()

        self.assertEqual(venta.total, Decimal('500.00'))

    def test_el_producto_inexistente_sigue_siendo_otro_error(self):
        """Son dos errores distintos para el operador."""
        with self.assertRaises(ProductoInexistenteError) as ctx:
            procesar_venta_service(
                usuario=self.cajera,
                datos={
                    'carrito': [{
                        'id': 999999, 'cantidad': 1,
                        'precio_venta': '500.00', 'descuento': '0.00',
                    }],
                    'metodo_pago': 'efectivo',
                    'total': '500.00',
                },
            )

        self.assertIn('no existe', str(ctx.exception))

    def test_la_busqueda_del_pos_no_muestra_categoria_inactiva(self):
        """
        La busqueda general exigia `categoria__activa=True` SOLO si el cliente
        mandaba filtro de categoria.
        """
        self.categoria.activa = False
        self.categoria.save(update_fields=['activa'])
        self.client.force_login(self.cajera)

        datos = self.client.get(
            reverse('pos:buscar_productos'), {'q': 'Martillo'},
        ).json()

        self.assertEqual(datos['productos'], [])

    def test_el_escaner_tampoco(self):
        self.categoria.activa = False
        self.categoria.save(update_fields=['activa'])
        self.client.force_login(self.cajera)

        respuesta = self.client.get(
            reverse('pos:producto_por_codigo', args=['PRO-001']),
        )

        self.assertEqual(respuesta.status_code, 404)

    def test_la_regla_es_una_sola(self):
        self.assertTrue(self.producto.es_vendible)
        self.assertIn(self.producto, productos_vendibles())

        self.categoria.activa = False
        self.categoria.save(update_fields=['activa'])
        self.producto.refresh_from_db()

        self.assertFalse(self.producto.es_vendible)
        self.assertNotIn(self.producto, productos_vendibles())


class CodigoDeBarrasNuloTests(ProductosTestCase):
    """PRO-008: dos productos sin codigo no bloquean el cursor."""

    def test_el_pull_conserva_null_y_no_colisiona(self):
        """
        La reproduccion: al bajar dos productos cloud con codigo nulo, el
        primero se guardaba como `''` y el segundo violaba
        `productos_codigo_barras_key`. El cursor `VersionMaestro` no avanzaba y
        la sucursal dejaba de recibir TODO el catalogo posterior.
        """
        uno = Producto.objects.create(
            sku='SIN-1', codigo_barras=None, nombre='Sin codigo 1',
            descripcion='', categoria=self.categoria,
            precio_venta=Decimal('10.00'), stock_minimo=1, activo=True,
            estado='nuevo', marca='', atributos={},
        )
        dos = Producto.objects.create(
            sku='SIN-2', codigo_barras=None, nombre='Sin codigo 2',
            descripcion='', categoria=self.categoria,
            precio_venta=Decimal('20.00'), stock_minimo=1, activo=True,
            estado='nuevo', marca='', atributos={},
        )

        self.assertIsNone(uno.codigo_barras)
        self.assertIsNone(dos.codigo_barras)

    def test_el_engine_normaliza_a_none_no_a_cadena_vacia(self):
        import inspect

        from apps.sync import engine

        fuente = inspect.getsource(engine)
        self.assertNotIn("'codigo_barras': item.get('codigo_barras') or ''", fuente)
        self.assertIn(
            "(item.get('codigo_barras') or '').strip() or None", fuente,
        )
