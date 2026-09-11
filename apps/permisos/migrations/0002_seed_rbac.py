"""
Data migration: siembra el catalogo de permisos y hace bootstrap del RBAC
para instalaciones existentes (idempotente).

- Siempre: upsert del catalogo de permisos.
- Si ya hay usuarios/sucursales (instalacion existente): crea un Negocio default
  desde ConfiguracionNegocio.nombre_negocio, enlaza sucursales/usuarios, crea los
  roles de sistema (Administrador / Cajero) y asigna rol a cada usuario segun su
  rol legacy.

En una BD fresca/de tests (sin usuarios ni sucursales) solo se siembra el
catalogo; el bootstrap del negocio se omite para no introducir datos fantasma.
"""
from django.db import migrations
from django.utils.text import slugify


# Snapshot del catalogo y preset existentes al crear esta migracion. No
# importar catalogo.py/seed.py: una migracion historica debe producir siempre
# el mismo resultado aunque el codigo vivo agregue permisos en el futuro.
CATALOGO_0002 = [
    ('clientes.ver', 'Ver clientes', 'clientes', 'Listar y consultar clientes.'),
    ('clientes.crear', 'Crear clientes', 'clientes', 'Registrar nuevos clientes.'),
    ('clientes.editar', 'Editar clientes', 'clientes', 'Modificar datos de clientes.'),
    ('clientes.eliminar', 'Eliminar clientes', 'clientes', 'Dar de baja clientes.'),
    ('productos.ver', 'Ver productos', 'productos', 'Listar y consultar productos.'),
    ('productos.crear', 'Crear productos', 'productos', 'Registrar nuevos productos.'),
    ('productos.editar', 'Editar productos', 'productos', 'Modificar productos.'),
    ('productos.eliminar', 'Eliminar productos', 'productos', 'Dar de baja productos.'),
    ('categorias.ver', 'Ver categorias', 'categorias', 'Listar y consultar categorias.'),
    ('categorias.crear', 'Crear categorias', 'categorias', 'Registrar categorias.'),
    ('categorias.editar', 'Editar categorias', 'categorias', 'Modificar categorias.'),
    ('categorias.eliminar', 'Eliminar categorias', 'categorias', 'Eliminar categorias.'),
    ('compras.ver', 'Ver compras', 'compras', 'Consultar compras e ingresos de mercancia.'),
    ('compras.registrar', 'Registrar compras', 'compras', 'Registrar compras (ingreso de lotes FIFO).'),
    ('inventario.ver', 'Ver inventario', 'inventario', 'Consultar stock y lotes.'),
    ('inventario.ajustar', 'Ajustar inventario', 'inventario', 'Ajustes manuales de inventario.'),
    ('ventas.crear', 'Registrar ventas', 'ventas', 'Procesar ventas en el POS.'),
    ('ventas.anular', 'Anular ventas', 'ventas', 'Anular ventas dentro del plazo permitido.'),
    ('ventas.aplicar_descuento', 'Aplicar descuentos', 'ventas', 'Aplicar descuentos en ventas.'),
    ('ventas.reimprimir', 'Reimprimir tickets', 'ventas', 'Reimprimir tickets de venta.'),
    ('cuentas_por_cobrar.ver', 'Ver cuentas por cobrar', 'cuentas_por_cobrar', 'Consultar cartera y cuentas por cobrar.'),
    ('reportes.ver', 'Ver reportes', 'reportes', 'Acceder a reportes y dashboard.'),
    ('reportes.consolidado.ver', 'Ver reporte consolidado', 'reportes', 'Ver reportes consolidados multi-sucursal.'),
    ('sucursales.ver', 'Ver sucursales', 'sucursales', 'Listar sucursales del negocio.'),
    ('permisos.administrar', 'Administrar roles y permisos', 'permisos', 'Crear/editar roles y asignar permisos a usuarios del negocio.'),
]
CAJERO_DEFAULT_0002 = [
    'ventas.crear', 'ventas.aplicar_descuento',
    'ventas.anular', 'ventas.reimprimir',
]


def _sembrar_catalogo(Permiso):
    for codigo, nombre, modulo, descripcion in CATALOGO_0002:
        Permiso.objects.update_or_create(
            codigo=codigo,
            defaults={
                'nombre': nombre,
                'modulo': modulo,
                'descripcion': descripcion,
            },
        )


def _slug_unico(Negocio, nombre):
    base = slugify(nombre)[:110] or 'negocio'
    slug = base
    numero = 2
    while Negocio.objects.filter(slug=slug).exists():
        slug = f'{base}-{numero}'
        numero += 1
    return slug


def _bootstrap_historico(
    *, Negocio, Sucursal, Usuario, Rol, Permiso, AsignacionRol, nombre,
):
    negocio = Negocio.objects.order_by('id').first()
    if negocio is None:
        nombre = nombre or 'Mi Negocio'
        negocio = Negocio.objects.create(
            nombre=nombre,
            slug=_slug_unico(Negocio, nombre),
            activo=True,
        )

    Sucursal.objects.filter(negocio__isnull=True).update(negocio=negocio)
    Usuario.objects.filter(negocio__isnull=True).update(negocio=negocio)

    admin, creado = Rol.objects.get_or_create(
        negocio=negocio,
        slug='administrador',
        defaults={
            'nombre': 'Administrador',
            'es_sistema': True,
            'descripcion': 'Acceso total a la configuracion del negocio.',
        },
    )
    if creado:
        admin.permisos.set(Permiso.objects.all())

    cajero, creado = Rol.objects.get_or_create(
        negocio=negocio,
        slug='cajero',
        defaults={
            'nombre': 'Cajero',
            'es_sistema': True,
            'descripcion': 'Operacion del POS (vender, descuento, reimprimir).',
        },
    )
    if creado:
        cajero.permisos.set(
            Permiso.objects.filter(codigo__in=CAJERO_DEFAULT_0002)
        )

    for usuario in Usuario.objects.filter(negocio=negocio):
        if usuario.rol in ('ADMIN', 'SYSADMIN'):
            rol = admin
        elif usuario.rol == 'CAJERA':
            rol = cajero
        else:
            continue
        AsignacionRol.objects.get_or_create(
            usuario=usuario,
            rol=rol,
            sucursal=None,
            defaults={'activo': True},
        )


def seed_rbac(apps, schema_editor):
    Permiso = apps.get_model('permisos', 'Permiso')
    Rol = apps.get_model('permisos', 'Rol')
    AsignacionRol = apps.get_model('permisos', 'AsignacionRol')
    Negocio = apps.get_model('negocios', 'Negocio')
    Sucursal = apps.get_model('sucursales', 'Sucursal')
    Usuario = apps.get_model('usuarios', 'Usuario')

    # 1. Catalogo (siempre).
    _sembrar_catalogo(Permiso)

    # 2. Bootstrap solo si hay datos existentes que migrar.
    if not (Usuario.objects.exists() or Sucursal.objects.exists()):
        return

    nombre = None
    try:
        ConfiguracionNegocio = apps.get_model('configuracion', 'ConfiguracionNegocio')
        cfg = (
            ConfiguracionNegocio.objects.exclude(nombre_negocio='')
            .order_by('id')
            .first()
        )
        if cfg and cfg.nombre_negocio:
            nombre = cfg.nombre_negocio
    except Exception:
        nombre = None

    _bootstrap_historico(
        Negocio=Negocio,
        Sucursal=Sucursal,
        Usuario=Usuario,
        Rol=Rol,
        Permiso=Permiso,
        AsignacionRol=AsignacionRol,
        nombre=nombre,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('permisos', '0001_initial'),
        ('usuarios', '0003_usuario_negocio'),
        ('sucursales', '0003_sucursal_negocio'),
        ('configuracion', '0006_accesorapidopos'),
    ]

    operations = [
        migrations.RunPython(seed_rbac, migrations.RunPython.noop),
    ]
