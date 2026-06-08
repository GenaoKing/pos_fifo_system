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


def asignar(usuario, rol, sucursal=None, set_negocio=True):
    """Asigna `rol` a `usuario` (opcionalmente acotado a `sucursal`).

    Por defecto fija tambien `usuario.negocio` al negocio del rol.
    """
    if set_negocio and usuario.negocio_id != rol.negocio_id:
        usuario.negocio = rol.negocio
        usuario.save(update_fields=['negocio'])
    return AsignacionRol.objects.create(usuario=usuario, rol=rol, sucursal=sucursal)
