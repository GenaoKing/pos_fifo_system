"""
utils/imagenes.py

Miniaturas de las imagenes de catalogo.

Las fotos entran por el POS de sucursal, casi siempre directas de un celular:
en Royal Plast pesaban **3.2 MB en promedio**. Todo lo que las muestra las
dibuja de 40x40 px — la grilla del portal, la lista de productos del POS y los
mosaicos del punto de venta. O sea que la pantalla de productos del portal
descargaba ~237 MB para pintar 73 cuadritos.

La miniatura es un JPEG derivado de 320 px de lado mayor: entre 15 y 30 KB,
mas de cien veces menos, y sigue viendose nitida en pantallas retina al doble
de densidad.

**El original no se toca.** La miniatura es un archivo aparte, en `thumbs/` al
lado de su original y dentro del mismo prefijo de tenant, y su nombre real vive
en `Producto.imagen_miniatura`. Nada adivina rutas: si el campo esta vacio, no
hay miniatura y se usa el original.
"""
import io
import logging
import posixpath

from django.core.files.base import ContentFile

logger = logging.getLogger('imagenes')


# 320 px cubre con holgura el uso real (40-48 px logicos) incluso en pantallas
# 3x, y deja margen para que la miniatura sirva tambien en vistas de detalle
# modestas sin tener que bajar el original.
LADO_MAX = 320

# 80 es el punto donde el artefacto JPEG deja de notarse a este tamano. Subirlo
# engorda el archivo sin ganancia visible.
CALIDAD = 80

CARPETA = 'thumbs'


def ruta_miniatura(nombre):
    """
    `royalplast/productos/foo.png` -> `royalplast/productos/thumbs/foo.jpg`

    La miniatura queda DENTRO del prefijo del tenant. Ponerla fuera mezclaria
    los archivos de un negocio con los de otro en el container compartido, que
    es justo lo que evita `apps/tenancy/media.py`.

    Siempre `.jpg`: la miniatura se genera en JPEG sea cual sea el original.
    Si dos originales distintos colapsan al mismo nombre, el storage
    desambigua solo y el nombre real se guarda en el campo.
    """
    nombre = str(nombre or '').replace('\\', '/').strip('/')
    if not nombre:
        return ''

    carpeta, _, archivo = nombre.rpartition('/')
    base, punto, _extension = archivo.rpartition('.')
    archivo = f'{base if punto else archivo}.jpg'
    return posixpath.join(carpeta, CARPETA, archivo) if carpeta else posixpath.join(CARPETA, archivo)


def generar_miniatura(archivo):
    """
    Devuelve un `ContentFile` JPEG, o `None` si el archivo no es una imagen
    utilizable.

    Devolver `None` en vez de reventar es deliberado: un producto con una foto
    corrupta tiene que poder guardarse igual. Se pierde la miniatura, no el
    producto.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        archivo.seek(0)
        with Image.open(archivo) as imagen:
            # Las fotos de celular traen la orientacion en EXIF y no en los
            # pixeles. El navegador la aplica al mostrar el original; si no se
            # aplica aca, la miniatura sale acostada mientras el original se ve
            # derecho, y parece un problema de la foto y no del recorte.
            imagen = ImageOps.exif_transpose(imagen)

            if imagen.mode in ('RGBA', 'LA', 'P'):
                # JPEG no tiene canal alfa: sin componer sobre blanco, un PNG
                # transparente sale con el fondo en negro.
                imagen = imagen.convert('RGBA')
                fondo = Image.new('RGB', imagen.size, (255, 255, 255))
                fondo.paste(imagen, mask=imagen.split()[-1])
                imagen = fondo
            elif imagen.mode != 'RGB':
                imagen = imagen.convert('RGB')

            imagen.thumbnail((LADO_MAX, LADO_MAX), Image.LANCZOS)

            buffer = io.BytesIO()
            imagen.save(
                buffer,
                format='JPEG',
                quality=CALIDAD,
                optimize=True,
                progressive=True,
            )
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.warning('No se pudo generar la miniatura: %s: %s', type(exc).__name__, exc)
        return None

    return ContentFile(buffer.getvalue())


def guardar_miniatura(campo_origen, fuente=None):
    """
    Genera la miniatura de un `FieldFile` y la guarda en su mismo storage.

    Devuelve el nombre REAL con el que quedo guardada — que puede no ser el
    calculado, porque el storage desambigua colisiones. Guardar el calculado
    dejaria el campo apuntando a un archivo inexistente.

    `fuente` es un archivo ya abierto con el mismo contenido del original, para
    leer de ahi en vez de traerlo del storage. Lo usa la migracion de media,
    que tiene el archivo en disco y acaba de subirlo: sin esto, cada imagen
    viajaria por la red dos veces — una al subir y otra al leerla de vuelta
    para recortarla.

    Cadena vacia si no habia origen o si la imagen no se pudo procesar.
    """
    if not campo_origen:
        return ''

    destino = ruta_miniatura(campo_origen.name)
    if not destino:
        return ''

    if fuente is not None:
        contenido = generar_miniatura(fuente)
        return campo_origen.storage.save(destino, contenido) if contenido else ''

    try:
        campo_origen.open('rb')
        contenido = generar_miniatura(campo_origen.file)
    except (OSError, ValueError) as exc:
        # El original puede no existir: base importada de un dump sin media,
        # o archivo borrado a mano. No es motivo para tumbar el guardado.
        logger.warning(
            'No se pudo leer el original "%s" para su miniatura: %s',
            campo_origen.name, exc,
        )
        return ''
    finally:
        try:
            campo_origen.close()
        except Exception:  # pragma: no cover - cerrar nunca debe enmascarar el fallo real
            pass

    if contenido is None:
        return ''

    return campo_origen.storage.save(destino, contenido)


# =====================================================================
# Validacion de subida (PRO-006)
# =====================================================================
#
# El endpoint de imagen asignaba `request.FILES['imagen']` directo al campo y
# guardaba: no comprobaba que fuera una imagen, ni su tipo, ni su tamano. Se
# subieron bytes HTML con `Content-Type: text/plain` y quedaron guardados y
# servidos desde media. Con eso, cualquier cuenta autenticada convierte el
# servidor —o el blob storage del tenant— en alojamiento de archivos
# arbitrarios, y segun dominio y cabeceras eso es superficie de phishing.
#
# `ImageField` NO valida por si solo cuando se asigna un archivo por codigo:
# la validacion vive en el formulario, y aca no hay formulario.

TAMANO_MAX_BYTES = 8 * 1024 * 1024  # 8 MB: una foto de celular entra holgada

FORMATOS_PERMITIDOS = {'JPEG', 'PNG', 'WEBP'}

EXTENSION_POR_FORMATO = {
    'JPEG': '.jpg',
    'PNG': '.png',
    'WEBP': '.webp',
}


class ImagenInvalida(ValueError):
    """El archivo subido no es una imagen aceptable."""


def validar_imagen_subida(archivo):
    """
    Comprueba que `archivo` sea realmente una imagen de un formato permitido.

    Devuelve el formato detectado. Lanza `ImagenInvalida` con un mensaje apto
    para mostrarle al operador.

    El tamano se mira ANTES de decodificar: decodificar es justamente lo caro, y
    un archivo enorme no deberia poder consumir memoria del worker solo para
    despues ser rechazado.
    """
    if archivo is None:
        raise ImagenInvalida('No se recibio ninguna imagen.')

    tamano = getattr(archivo, 'size', None)
    if tamano is not None and tamano > TAMANO_MAX_BYTES:
        mb = TAMANO_MAX_BYTES // (1024 * 1024)
        raise ImagenInvalida(f'La imagen supera el maximo de {mb} MB.')

    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow es dependencia del proyecto
        raise ImagenInvalida('No se puede validar la imagen en este servidor.')

    try:
        archivo.seek(0)
        with Image.open(archivo) as imagen:
            # `verify()` detecta el archivo corrupto o que no es imagen; despues
            # el objeto queda inutilizable, por eso solo se lee el formato.
            formato = (imagen.format or '').upper()
            imagen.verify()
    except ImagenInvalida:
        raise
    except Exception:
        raise ImagenInvalida('El archivo no es una imagen valida.')
    finally:
        try:
            archivo.seek(0)
        except Exception:
            pass

    if formato not in FORMATOS_PERMITIDOS:
        permitidos = ', '.join(sorted(FORMATOS_PERMITIDOS))
        raise ImagenInvalida(
            f'Formato "{formato or "desconocido"}" no permitido. '
            f'Usa {permitidos}.'
        )

    return formato


def nombre_seguro(formato, prefijo='producto'):
    """
    Nombre de archivo generado del lado servidor.

    El nombre que envia el cliente no se usa: puede traer rutas, caracteres de
    control o una extension que no corresponde al contenido real.
    """
    import secrets

    extension = EXTENSION_POR_FORMATO.get(formato, '.jpg')
    return f'{prefijo}_{secrets.token_hex(8)}{extension}'
