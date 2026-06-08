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


def seed_rbac(apps, schema_editor):
    Permiso = apps.get_model('permisos', 'Permiso')
    Rol = apps.get_model('permisos', 'Rol')
    AsignacionRol = apps.get_model('permisos', 'AsignacionRol')
    Negocio = apps.get_model('negocios', 'Negocio')
    Sucursal = apps.get_model('sucursales', 'Sucursal')
    Usuario = apps.get_model('usuarios', 'Usuario')

    from apps.permisos import seed as seed_helpers
    from apps.permisos.catalogo import sembrar_catalogo

    # 1. Catalogo (siempre).
    sembrar_catalogo(Permiso)

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

    seed_helpers.bootstrap(
        NegocioModel=Negocio,
        SucursalModel=Sucursal,
        UsuarioModel=Usuario,
        RolModel=Rol,
        PermisoModel=Permiso,
        AsignacionRolModel=AsignacionRol,
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
