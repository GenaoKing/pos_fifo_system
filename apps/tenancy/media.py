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

    prefix = ''
    try:
        from .models import Tenant

        tenant = Tenant.objects.using('default').filter(
            tenant_key=tenant_key,
            activo=True,
        ).only('media_prefix').first()
        prefix = tenant.media_prefix if tenant else ''
    except Exception:
        prefix = ''

    prefix = normalize_media_name(prefix or f'{tenant_key}/')
    return f'{prefix}/' if prefix else ''


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
