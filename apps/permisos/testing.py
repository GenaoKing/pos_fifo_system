"""
apps/permisos/testing.py
Helpers para armar fixtures RBAC en tests (importables desde cualquier app).

No es codigo de produccion; centraliza el boilerplate de crear negocios, roles
con permisos y asignaciones.
"""
from django.utils.text import slugify

from apps.negocios.models import Negocio
from apps.permisos.catalogo import sembrar_catalogo
from apps.permisos.models import AsignacionRol, Permiso, Rol

# Minimo para cerrar una venta desde el service.
PERMISOS_VENTA = ('ventas.crear', 'ventas.aplicar_descuento')


def crear_negocio(nombre):
    """Crea un Negocio con slug derivado del nombre."""
    return Negocio.objects.create(nombre=nombre, slug=slugify(nombre))


def crear_rol(negocio, nombre, permisos_codigos=()):
    """Crea un Rol en el negocio y le asigna los permisos indicados (por codigo)."""
    rol = Rol.objects.create(negocio=negocio, nombre=nombre, slug=slugify(nombre))
    if permisos_codigos:
        # Idempotente: garantiza que el catalogo exista (en tests fuera de la
        # data migration, o si se limpio la tabla).
        sembrar_catalogo(Permiso)
        rol.permisos.set(Permiso.objects.filter(codigo__in=permisos_codigos))
    return rol


def habilitar_cajero(usuario, *, negocio=None, permisos=None, sucursal=None):
    """
    Da a `usuario` un rol de cajero con los permisos operativos por defecto.

    Atajo para tests que ejercitan flujos del POS: `procesar_venta_service`
    exige `ventas.crear` server-side (y `ventas.aplicar_descuento` si el
    carrito trae descuentos), igual que en una instalacion real, donde el
    bootstrap RBAC ya deja al cajero con PERMISOS_CAJERO_DEFAULT.

    Por defecto otorga SOLO los permisos de venta, no el set completo del
    cajero: asi un test que verifica el gate de otro permiso (ej. CxC) sigue
    viendo a este usuario sin ese permiso. Pasa `permisos=` para ampliarlo.

    Cada llamada crea su propio Negocio salvo que se pase uno, para que dos
    fixtures del mismo modulo no choquen por el slug del rol.
    """
    negocio = negocio or crear_negocio(f'Negocio {usuario.username}')
    rol = crear_rol(
        negocio,
        f'Cajero {usuario.username}',
        permisos if permisos is not None else PERMISOS_VENTA,
    )
    return asignar(usuario, rol, sucursal=sucursal)



def asignar(usuario, rol, sucursal=None, set_negocio=True):
    """Asigna `rol` a `usuario` (opcionalmente acotado a `sucursal`).

    Por defecto fija tambien `usuario.negocio` al negocio del rol.
    """
    if set_negocio and usuario.negocio_id != rol.negocio_id:
        usuario.negocio = rol.negocio
        usuario.save(update_fields=['negocio'])
    return AsignacionRol.objects.create(usuario=usuario, rol=rol, sucursal=sucursal)
