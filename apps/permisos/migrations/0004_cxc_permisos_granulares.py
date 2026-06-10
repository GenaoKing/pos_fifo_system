"""
Data migration: permisos granulares de CxC (cobrar / anular_pago).

- Upsert del catalogo (agrega cuentas_por_cobrar.cobrar y .anular_pago).
- Roles de sistema existentes:
    * Cajero: + ver/cobrar (preserva la conducta actual del POS, donde la
      cajera consulta cartera y registra abonos; antes solo habia
      @login_required). anular_pago NO se agrega.
    * Administrador: + los tres permisos de CxC (el rol se creo con el
      catalogo de su epoca; los ADMIN reales igual tienen es_acceso_total).
Idempotente.
"""
from django.db import migrations

CAJERO_NUEVOS = ['cuentas_por_cobrar.ver', 'cuentas_por_cobrar.cobrar']
ADMIN_NUEVOS = ['cuentas_por_cobrar.ver', 'cuentas_por_cobrar.cobrar', 'cuentas_por_cobrar.anular_pago']


def aplicar(apps, schema_editor):
    Permiso = apps.get_model('permisos', 'Permiso')
    Rol = apps.get_model('permisos', 'Rol')

    from apps.permisos.catalogo import sembrar_catalogo

    sembrar_catalogo(Permiso)

    permisos_cajero = list(Permiso.objects.filter(codigo__in=CAJERO_NUEVOS))
    for rol in Rol.objects.filter(es_sistema=True, slug='cajero'):
        rol.permisos.add(*permisos_cajero)

    permisos_admin = list(Permiso.objects.filter(codigo__in=ADMIN_NUEVOS))
    for rol in Rol.objects.filter(es_sistema=True, slug='administrador'):
        rol.permisos.add(*permisos_admin)


def revertir(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('permisos', '0003_asignacionrol_fecha_modificacion'),
    ]

    operations = [
        migrations.RunPython(aplicar, revertir),
    ]
