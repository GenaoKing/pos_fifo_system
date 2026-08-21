"""
Data migration: permiso `ventas.autorizar_descuento`.

- Upsert del catalogo (agrega ventas.autorizar_descuento).
- Rol Administrador de sistema: + el permiso. Sin esto, una instalacion viva
  se queda sin NADIE que pueda autorizar descuentos, porque
  `seed.crear_roles_default` solo fija permisos cuando el rol SE CREA
  (ver apps/permisos/seed.py) y esos roles ya existen.
- Rol Cajero: NO. El gate existe precisamente para que el cajero no se
  autorice a si mismo.

Idempotente.
"""
from django.db import migrations

PERMISO = 'ventas.autorizar_descuento'


def aplicar(apps, schema_editor):
    Permiso = apps.get_model('permisos', 'Permiso')
    Rol = apps.get_model('permisos', 'Rol')

    from apps.permisos.catalogo import sembrar_catalogo

    sembrar_catalogo(Permiso)

    permiso = Permiso.objects.filter(codigo=PERMISO).first()
    if permiso is None:
        return

    for rol in Rol.objects.filter(es_sistema=True, slug='administrador'):
        rol.permisos.add(permiso)


def revertir(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('permisos', '0006_credencial_fisica_y_descuento'),
    ]

    operations = [
        migrations.RunPython(aplicar, revertir),
    ]
