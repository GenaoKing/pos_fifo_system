"""
apps/permisos/seed.py
Helpers reutilizables para sembrar el RBAC inicial.

Se escriben para funcionar tanto con los modelos reales como con los modelos
historicos de una data migration (apps.get_model). Por eso NO se apoyan en
metodos custom del modelo (ej. Negocio.save autogenera slug); calculan el slug
explicitamente.
"""
from django.db import router, transaction
from django.utils.text import slugify

from .catalogo import PERMISOS_CAJERO_DEFAULT, sembrar_catalogo


def _slug_unico(NegocioModel, nombre, *, using):
    base = slugify(nombre)[:110] or 'negocio'
    slug = base
    i = 2
    while NegocioModel.objects.using(using).filter(slug=slug).exists():
        slug = f'{base}-{i}'
        i += 1
    return slug


def crear_roles_default(negocio, RolModel, PermisoModel, *, using=None):
    """
    Crea (idempotente) los roles de sistema del negocio:
      - Administrador: todos los permisos del catalogo (plantilla inicial).
      - Cajero: operacion basica del POS (ver PERMISOS_CAJERO_DEFAULT).
    Retorna (admin_rol, cajero_rol).

    Los permisos se fijan SOLO al crear el rol: re-ejecutar el bootstrap NO
    pisa las personalizaciones que un admin haya hecho desde el portal. Nota:
    los usuarios ADMIN/SYSADMIN tienen acceso total por `es_acceso_total`
    independientemente de los permisos del rol Administrador.
    """
    using = using or negocio._state.db or router.db_for_write(RolModel)
    admin_rol, admin_creado = RolModel.objects.using(using).get_or_create(
        negocio=negocio,
        slug='administrador',
        defaults={
            'nombre': 'Administrador',
            'es_sistema': True,
            'descripcion': 'Acceso total a la configuracion del negocio.',
        },
    )
    if admin_creado:
        admin_rol.permisos.set(PermisoModel.objects.using(using).all())

    cajero_rol, cajero_creado = RolModel.objects.using(using).get_or_create(
        negocio=negocio,
        slug='cajero',
        defaults={
            'nombre': 'Cajero',
            'es_sistema': True,
            'descripcion': 'Operacion del POS (vender, descuento, reimprimir).',
        },
    )
    if cajero_creado:
        cajero_rol.permisos.set(
            PermisoModel.objects.using(using).filter(
                codigo__in=PERMISOS_CAJERO_DEFAULT,
            )
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
    negocio=None,
    using=None,
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
    using = (
        using
        or getattr(getattr(negocio, '_state', None), 'db', None)
        or router.db_for_write(NegocioModel)
        or 'default'
    )
    with transaction.atomic(using=using):
        return _bootstrap_en_transaccion(
            NegocioModel=NegocioModel,
            SucursalModel=SucursalModel,
            UsuarioModel=UsuarioModel,
            RolModel=RolModel,
            PermisoModel=PermisoModel,
            AsignacionRolModel=AsignacionRolModel,
            nombre=nombre,
            negocio=negocio,
            using=using,
        )


def _bootstrap_en_transaccion(
    *, NegocioModel, SucursalModel, UsuarioModel, RolModel, PermisoModel,
    AsignacionRolModel, nombre, negocio, using,
):
    sembrar_catalogo(PermisoModel, using=using)

    negocios = NegocioModel.objects.using(using).order_by('id')
    total_negocios = negocios.count()
    if negocio is None and total_negocios > 1:
        raise ValueError(
            'Hay varios negocios: el bootstrap no puede elegir uno por orden. '
            'Indica el negocio explicitamente.'
        )
    if negocio is None:
        negocio = negocios.first()
    if negocio is None:
        nombre = nombre or 'Mi Negocio'
        negocio = NegocioModel.objects.using(using).create(
            nombre=nombre,
            slug=_slug_unico(NegocioModel, nombre, using=using),
            activo=True,
        )

        total_negocios = 1

    # La adopcion masiva solo es determinista en una instalacion con un unico
    # negocio. Con varios tenants, los huerfanos requieren una correccion
    # explicita y nunca se asignan al primero por orden de PK.
    if total_negocios <= 1:
        SucursalModel.objects.using(using).filter(
            negocio__isnull=True,
        ).update(negocio=negocio)
        UsuarioModel.objects.using(using).filter(
            negocio__isnull=True,
        ).update(negocio=negocio)

    admin_rol, cajero_rol = crear_roles_default(
        negocio, RolModel, PermisoModel, using=using,
    )

    for usuario in UsuarioModel.objects.using(using).filter(negocio=negocio):
        rol_legacy = getattr(usuario, 'rol', None)
        if rol_legacy in ('ADMIN', 'SYSADMIN'):
            destino = admin_rol
        elif rol_legacy == 'CAJERA':
            destino = cajero_rol
        else:
            continue
        AsignacionRolModel.objects.using(using).get_or_create(
            usuario=usuario,
            rol=destino,
            sucursal=None,
            defaults={'activo': True},
        )

    return negocio
