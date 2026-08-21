"""
apps/reportes/almacenamiento.py

Donde viven los documentos financieros generados por reportes.

Dos hallazgos convergen aca:

RPT-001 - Los cierres se guardaban en `MEDIA_ROOT/reportes/cierres/` y
`config/urls.py` publica todo `MEDIA_ROOT` con `django.views.static.serve`, sin
login y sin condicionarlo a DEBUG. El endpoint oficial exigia permiso, pero el
mismo archivo se bajaba por `/media/reportes/cierres/cierre_20260820.pdf`, un
nombre que se enumera probando fechas. Cualquiera que conociera el host se
llevaba las ventas, los cobros y el desglose por cajero de todos los dias.

RPT-007 - El nombre solo incorporaba la fecha. Dos tenants que cerraban el mismo
dia escribian exactamente el mismo path: el PDF del segundo pisaba al del
primero y ambos registros apuntaban al archivo sobreviviente. Combinado con
RPT-001, la colision se convertia en fuga entre negocios.

La respuesta a ambos: los documentos financieros NO son media. Viven fuera de
`MEDIA_ROOT`, bajo el prefijo del tenant, con el id del cierre y un sufijo
aleatorio en el nombre — y se entregan unicamente por la vista autorizada.
"""
import os
import secrets

from django.conf import settings

SUBDIR_CIERRES = 'cierres'


def raiz_privada():
    """
    Directorio raiz de documentos privados.

    Configurable con `REPORTES_PRIVATE_ROOT`. El default cuelga de `BASE_DIR`,
    NO de `MEDIA_ROOT`: si alguien apunta esta raiz dentro de media vuelve a
    publicar los cierres, asi que eso se rechaza explicitamente.
    """
    raiz = getattr(settings, 'REPORTES_PRIVATE_ROOT', None)
    if not raiz:
        raiz = os.path.join(str(settings.BASE_DIR), 'private', 'reportes')
    raiz = os.path.abspath(str(raiz))

    media = os.path.abspath(str(settings.MEDIA_ROOT))
    if raiz == media or raiz.startswith(media + os.sep):
        raise ValueError(
            'REPORTES_PRIVATE_ROOT no puede estar dentro de MEDIA_ROOT: '
            'MEDIA_ROOT se sirve publicamente y los cierres quedarian '
            'descargables sin autenticacion.'
        )
    return raiz


def _prefijo_tenant():
    """Prefijo del tenant activo, o '' cuando la tenancy esta apagada."""
    from apps.tenancy.context import tenancy_enabled

    if not tenancy_enabled():
        return ''

    from apps.tenancy.media import tenant_media_prefix

    # `tenant_media_prefix` falla fuerte si no hay tenant en contexto, que es lo
    # correcto: bajo tenancy, escribir sin prefijo mezcla negocios.
    return tenant_media_prefix().strip('/')


def ruta_cierre(cierre):
    """
    Path absoluto del PDF de un cierre, creando el directorio si hace falta.

    El nombre incluye el id y un sufijo aleatorio: aunque el archivo terminara
    expuesto por un error de configuracion, ya no se enumera probando fechas.
    """
    partes = [raiz_privada()]
    prefijo = _prefijo_tenant()
    if prefijo:
        partes.append(prefijo)
    partes.append(SUBDIR_CIERRES)

    directorio = os.path.join(*partes)
    os.makedirs(directorio, exist_ok=True)

    nombre = (
        f"cierre_{cierre.fecha.strftime('%Y%m%d')}"
        f"_{cierre.pk}_{secrets.token_hex(8)}.pdf"
    )
    return os.path.join(directorio, nombre)


def es_ruta_privada(path):
    """True si `path` esta bajo la raiz privada (control de la vista de descarga)."""
    if not path:
        return False
    try:
        raiz = raiz_privada()
    except ValueError:
        return False
    destino = os.path.abspath(str(path))
    return destino == raiz or destino.startswith(raiz + os.sep)
