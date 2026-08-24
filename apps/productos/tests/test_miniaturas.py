"""
Miniaturas del catalogo.

Existen porque el portal descargaba ~237 MB de originales de celular para
pintar 73 cuadritos de 40x40 px. Lo que se prueba aca es justo lo que hace que
esa cuenta no vuelva: que la miniatura se genere sola, que no se regenere
cuando no hace falta, y que nadie se quede sin imagen si la generacion falla.
"""
import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.productos.models import Categoria, Producto
from utils.imagenes import LADO_MAX, generar_miniatura, ruta_miniatura


def imagen_jpeg(ancho=2400, alto=1600, color=(200, 30, 30)):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', (ancho, alto), color).save(buffer, format='JPEG', quality=95)
    return buffer.getvalue()


def imagen_png_transparente(lado=800):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGBA', (lado, lado), (0, 0, 0, 0)).save(buffer, format='PNG')
    return buffer.getvalue()


class RutaMiniaturaTests(TestCase):
    def test_queda_en_thumbs_junto_al_original(self):
        self.assertEqual(
            ruta_miniatura('productos/foto.jpg'),
            'productos/thumbs/foto.jpg',
        )

    def test_conserva_el_prefijo_del_tenant(self):
        """
        Fuera del prefijo, los archivos de un negocio se mezclarian con los de
        otro en el container compartido.
        """
        self.assertEqual(
            ruta_miniatura('royalplast/productos/foto.png'),
            'royalplast/productos/thumbs/foto.jpg',
        )

    def test_siempre_jpg_sea_cual_sea_el_original(self):
        self.assertTrue(ruta_miniatura('productos/foto.png').endswith('.jpg'))
        self.assertTrue(ruta_miniatura('productos/foto.webp').endswith('.jpg'))
        self.assertTrue(ruta_miniatura('productos/sin_extension').endswith('.jpg'))

    def test_nombre_vacio_no_inventa_ruta(self):
        self.assertEqual(ruta_miniatura(''), '')
        self.assertEqual(ruta_miniatura(None), '')


class GenerarMiniaturaTests(TestCase):
    def test_reduce_al_lado_maximo_y_pesa_una_fraccion(self):
        from PIL import Image

        original = imagen_jpeg()
        miniatura = generar_miniatura(io.BytesIO(original))
        self.assertIsNotNone(miniatura)

        with Image.open(io.BytesIO(miniatura.read())) as imagen:
            self.assertLessEqual(max(imagen.size), LADO_MAX)
            self.assertEqual(imagen.format, 'JPEG')

        miniatura.seek(0)
        self.assertLess(
            len(miniatura.read()), len(original) / 10,
            'la miniatura debe pesar un orden de magnitud menos: es toda la razon de existir',
        )

    def test_transparencia_va_sobre_blanco_y_no_sobre_negro(self):
        """JPEG no tiene alfa; sin componer, un PNG transparente sale negro."""
        from PIL import Image

        miniatura = generar_miniatura(io.BytesIO(imagen_png_transparente()))
        self.assertIsNotNone(miniatura)
        with Image.open(io.BytesIO(miniatura.read())) as imagen:
            self.assertEqual(imagen.convert('RGB').getpixel((5, 5)), (255, 255, 255))

    def test_orientacion_exif_se_aplica_a_los_pixeles(self):
        """
        Las fotos de celular guardan la rotacion en EXIF. El navegador la aplica
        al original; si la miniatura no la aplica, sale acostada y parece un
        problema de la foto.
        """
        from PIL import Image

        buffer = io.BytesIO()
        imagen = Image.new('RGB', (1200, 600), (10, 10, 200))
        exif = imagen.getexif()
        exif[274] = 6  # Orientation: rotar 90 grados
        imagen.save(buffer, format='JPEG', exif=exif)

        miniatura = generar_miniatura(io.BytesIO(buffer.getvalue()))
        with Image.open(io.BytesIO(miniatura.read())) as resultado:
            ancho, alto = resultado.size
        self.assertGreater(alto, ancho, 'la orientacion EXIF debe quedar en los pixeles')

    def test_archivo_que_no_es_imagen_devuelve_none_sin_reventar(self):
        """Un producto con una foto corrupta tiene que poder guardarse igual."""
        self.assertIsNone(generar_miniatura(io.BytesIO(b'esto no es una imagen')))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='miniaturas-'))
class ProductoMiniaturaTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings

        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.categoria = Categoria.objects.create(nombre='Plasticos')

    def _producto(self, sku='SKU-1', con_imagen=True):
        producto = Producto(
            sku=sku,
            nombre='Silla',
            categoria=self.categoria,
            precio_venta=100,
        )
        if con_imagen:
            producto.imagen = SimpleUploadedFile('foto.jpg', imagen_jpeg(), 'image/jpeg')
        producto.save()
        return producto

    def test_guardar_con_imagen_genera_la_miniatura(self):
        producto = self._producto()
        self.assertTrue(producto.imagen_miniatura)
        self.assertIn('thumbs/', producto.imagen_miniatura.name)
        self.assertLess(producto.imagen_miniatura.size, producto.imagen.size / 10)

        # Y quedo persistida, no solo en la instancia en memoria.
        producto.refresh_from_db()
        self.assertTrue(producto.imagen_miniatura)

    def test_guardar_sin_tocar_la_imagen_no_regenera(self):
        """
        Sin esta guarda, cada guardado de producto se lleva una lectura del
        blob original por la red.
        """
        producto = self._producto()
        primera = producto.imagen_miniatura.name

        producto = Producto.objects.get(pk=producto.pk)
        producto.precio_venta = 250
        producto.save()

        producto.refresh_from_db()
        self.assertEqual(producto.imagen_miniatura.name, primera)

    def test_cambiar_la_imagen_regenera_y_borra_la_anterior(self):
        producto = self._producto()
        anterior = producto.imagen_miniatura.name
        almacen = producto.imagen_miniatura.storage

        producto = Producto.objects.get(pk=producto.pk)
        producto.imagen = SimpleUploadedFile('otra.jpg', imagen_jpeg(color=(20, 180, 60)), 'image/jpeg')
        producto.save()

        producto.refresh_from_db()
        self.assertTrue(producto.imagen_miniatura)
        self.assertNotEqual(producto.imagen_miniatura.name, anterior)
        self.assertFalse(
            almacen.exists(anterior),
            'la miniatura vieja ya no la referencia nadie: dejarla acumula basura',
        )

    def test_producto_sin_imagen_no_tiene_miniatura(self):
        producto = self._producto(con_imagen=False)
        self.assertFalse(producto.imagen_miniatura)
        self.assertIsNone(producto.imagen_preview)

    def test_imagen_preview_prefiere_la_miniatura(self):
        producto = self._producto()
        self.assertEqual(producto.imagen_preview.name, producto.imagen_miniatura.name)

    def test_imagen_preview_cae_al_original_si_no_hay_miniatura(self):
        """
        Es el estado de todo catalogo anterior a este cambio: mostrar el
        original pesado es peor que no mostrar nada, pero mucho mejor que
        pedir un archivo que no existe.
        """
        producto = self._producto()
        Producto.objects.filter(pk=producto.pk).update(imagen_miniatura=None)
        producto.refresh_from_db()
        self.assertEqual(producto.imagen_preview.name, producto.imagen.name)

    def test_imagen_ilegible_no_impide_guardar_el_producto(self):
        producto = Producto(
            sku='SKU-ROTO',
            nombre='Foto corrupta',
            categoria=self.categoria,
            precio_venta=50,
            imagen=SimpleUploadedFile('roto.jpg', b'no soy un jpeg', 'image/jpeg'),
        )
        producto.save()

        self.assertTrue(Producto.objects.filter(sku='SKU-ROTO').exists())
        self.assertFalse(producto.imagen_miniatura)
        # Y lo que se muestra sigue siendo algo: el original.
        self.assertEqual(producto.imagen_preview.name, producto.imagen.name)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='miniaturas-cmd-'))
class GenerarMiniaturasCommandTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings

        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.categoria = Categoria.objects.create(nombre='Plasticos')
        self.producto = Producto.objects.create(
            sku='SKU-BACK',
            nombre='Mesa',
            categoria=self.categoria,
            precio_venta=100,
            imagen=SimpleUploadedFile('mesa.jpg', imagen_jpeg(), 'image/jpeg'),
        )
        # Simular el catalogo heredado: imagen si, miniatura no.
        Producto.objects.filter(pk=self.producto.pk).update(imagen_miniatura=None)

    def _correr(self, **opciones):
        from django.core.management import call_command

        salida = io.StringIO()
        call_command('generar_miniaturas', stdout=salida, **opciones)
        return salida.getvalue()

    def test_dry_run_no_escribe_nada(self):
        salida = self._correr()
        self.assertIn('DRY-RUN', salida)
        self.producto.refresh_from_db()
        self.assertFalse(self.producto.imagen_miniatura)

    def test_apply_genera_las_faltantes(self):
        salida = self._correr(apply=True)
        self.producto.refresh_from_db()
        self.assertTrue(self.producto.imagen_miniatura)
        self.assertIn('generadas: 1', salida)

    def test_si_fallan_todas_el_comando_falla(self):
        """
        Corriendo el backfill contra un tenant cuyos originales no estaban en
        el storage, las 73 fallaron y el comando salio en 0: un runbook
        encadenado lo habria dado por bueno.
        """
        from django.core.management.base import CommandError

        # Dejar el campo apuntando a un archivo que no existe.
        Producto.objects.filter(pk=self.producto.pk).update(
            imagen='productos/no-existe.jpg', imagen_miniatura=None,
        )
        with self.assertRaises(CommandError) as caso:
            self._correr(apply=True)
        self.assertIn('Ninguna de las 1 miniaturas', str(caso.exception))

    def test_una_que_falla_entre_varias_no_tumba_el_resto(self):
        """Un catalogo con una foto rota se migra igual; se reporta el fallo."""
        Producto.objects.create(
            sku='SKU-ROTO-2',
            nombre='Foto perdida',
            categoria=self.categoria,
            precio_venta=10,
            imagen='productos/no-existe.jpg',
        )
        salida = self._correr(apply=True)
        self.assertIn('generadas: 1', salida)
        self.assertIn('fallidas:  1', salida)

    def test_segunda_corrida_no_tiene_nada_que_hacer(self):
        """Idempotente: se puede volver a correr sin regenerar ni duplicar."""
        self._correr(apply=True)
        self.producto.refresh_from_db()
        nombre = self.producto.imagen_miniatura.name

        salida = self._correr(apply=True)
        self.assertIn('No hay productos con imagen pendientes', salida)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.imagen_miniatura.name, nombre)
