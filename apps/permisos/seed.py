"""
apps/permisos/seed.py
Helpers reutilizables para sembrar el RBAC inicial.

Se escriben para funcionar tanto con los modelos reales como con los modelos
historicos de una data migration (apps.get_model). Por eso NO se apoyan en
metodos custom del modelo (ej. Negocio.save autogenera slug); calculan el slug
explicitamente.
"""
from django.utils.text import slugify

from .catalogo import PERMISOS_CAJERO_DEFAULT, sembrar_catalogo


def _slug_unico(NegocioModel, nombre):
    base = slugify(nombre)[:110] or 'negocio'
    slug = base
    i = 2
    while NegocioModel.objects.filter(slug=slug).exists():
        slug = f'{base}-{i}'
        i += 1
    return slug


def crear_roles_default(negocio, RolModel, PermisoModel):
    """
    Crea (idempotente) los roles de sistema del negocio:
      - Administrador: todos los permisos del catalogo.
      - Cajero: replica la conducta hardcoded historica de CAJERA.
    Retorna (admin_rol, cajero_rol).
    """
    admin_rol, _ = RolModel.objects.get_or_create(
        negocio=negocio,
        slug='administrador',
        defaults={
            'nombre': 'Administrador',
            'es_sistema': True,
            'descripcion': 'Acceso total a la configuracion del negocio.',
        },
    )
    admin_rol.permisos.set(PermisoModel.objects.all())

    cajero_rol, _ = RolModel.objects.get_or_create(
        negocio=negocio,
        slug='cajero',
        defaults={
            'nombre': 'Cajero',
            'es_sistema': True,
            'descripcion': 'Operacion del POS. Replica la conducta historica del rol CAJERA.',
        },
    )
    cajero_rol.permisos.set(
        PermisoModel.objects.filter(codigo__in=PERMISOS_CAJERO_DEFAULT)
    )
    return admin_rol, cajero_rol


def bootstrap(
    *,
    NegocioModel,
    SucursalModel,
    UsuarioModel,
    RolModel,
    PermisoModel,
    AsignacionRolModel,
    nombre=None,
):
    """
    Bootstrap del RBAC para una instalacion existente (idempotente):
      1. Siembra el catalogo de permisos.
      2. Crea (si no existe) un Negocio default y enlaza sucursales/usuarios huerfanos.
      3. Crea roles de sistema (Administrador / Cajero).
      4. Asigna rol a cada usuario segun su rol legacy (ADMIN/SYSADMIN -> Administrador,
         CAJERA -> Cajero).

    Retorna el Negocio.
    """
    sembrar_catalogo(PermisoModel)

    negocio = NegocioModel.objects.order_by('id').first()
    if negocio is None:
        nombre = nombre or 'Mi Negocio'
        negocio = NegocioModel.objects.create(
            nombre=nombre,
            slug=_slug_unico(NegocioModel, nombre),
            activo=True,
        )

    SucursalModel.objects.filter(negocio__isnull=True).update(negocio=negocio)
    UsuarioModel.objects.filter(negocio__isnull=True).update(negocio=negocio)

    admin_rol, cajero_rol = crear_roles_default(negocio, RolModel, PermisoModel)

    for usuario in UsuarioModel.objects.filter(negocio=negocio):
        rol_legacy = getattr(usuario, 'rol', None)
        if rol_legacy in ('ADMIN', 'SYSADMIN'):
            destino = admin_rol
        elif rol_legacy == 'CAJERA':
            destino = cajero_rol
        else:
            continue
        AsignacionRolModel.objects.get_or_create(
            usuario=usuario,
            rol=destino,
            sucursal=None,
            defaults={'activo': True},
        )

    return negocio
