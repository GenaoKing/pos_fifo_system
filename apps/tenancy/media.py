import posixpath

from .context import TenantContextError, get_current_tenant_key, tenancy_enabled


def normalize_media_name(name):
    raw = str(name or '').replace('\\', '/')
    parts = [
        part
        for part in raw.split('/')
        if part and part not in {'.', '..'}
    ]
    return '/'.join(parts)


def tenant_media_prefix():
    if not tenancy_enabled():
        return ''

    tenant_key = get_current_tenant_key()
    if not tenant_key:
        # Fail-loud (como el router): bajo tenancy nunca guardamos media sin
        # prefijo en el container compartido. Si no hay tenant activo, es un bug
        # de contexto, no algo a degradar en silencio.
        raise TenantContextError(
            'Ruta de media de tenant solicitada sin tenant activo en contexto.'
        )

    from .models import Tenant

    # Sin `try/except Exception`. Antes cualquier fallo al leer el control plane
    # —BD caida, migracion pendiente, bug— se degradaba en silencio al prefijo
    # derivado del key. Eso guarda archivos bajo un namespace DISTINTO del
    # configurado y esconde la causa raiz: el sintoma aparece semanas despues
    # como "las imagenes no se ven".
    #
    # Un error de infraestructura tiene que propagarse; solo la ausencia de
    # configuracion (tenant sin prefijo) se cubre con el default derivado.
    tenant = Tenant.objects.using('default').filter(
        tenant_key=tenant_key,
        activo=True,
    ).only('media_prefix').first()

    if tenant is None:
        raise TenantContextError(
            f'No hay tenant activo "{tenant_key}" en el control plane; '
            f'no se puede resolver el namespace de media.'
        )

    prefix = normalize_media_name(tenant.media_prefix or f'{tenant_key}/')
    if not prefix:
        raise TenantContextError(
            f'El tenant "{tenant_key}" tiene un media_prefix vacio o invalido; '
            f'guardar sin prefijo mezclaria sus archivos con los de otro negocio.'
        )
    return f'{prefix}/'


def tenant_media_name(directory, filename):
    directory = normalize_media_name(directory)
    filename = normalize_media_name(filename)
    prefix = tenant_media_prefix()

    if not filename:
        filename = 'archivo'

    if prefix and filename.startswith(prefix):
        return filename

    if directory and filename.startswith(f'{directory}/'):
        relative_name = filename
    else:
        relative_name = posixpath.join(directory, posixpath.basename(filename))

    return f'{prefix}{relative_name}' if prefix else relative_name


def producto_image_upload_to(instance, filename):
    return tenant_media_name('productos', filename)


def config_logo_upload_to(instance, filename):
    return tenant_media_name('config', filename)
