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
FILA_PERMISO = (
    PERMISO,
    'Autorizar descuentos',
    'ventas',
    'Emitir la autorizacion que habilita un descuento por encima de la '
    'tolerancia configurada. Quien lo tiene tambien descuenta sin pedir '
    'autorizacion a nadie: el gate solo aplica a quien NO lo tiene.',
)


def aplicar(apps, schema_editor):
    Permiso = apps.get_model('permisos', 'Permiso')
    Rol = apps.get_model('permisos', 'Rol')

    codigo, nombre, modulo, descripcion = FILA_PERMISO
    Permiso.objects.update_or_create(
        codigo=codigo,
        defaults={
            'nombre': nombre, 'modulo': modulo, 'descripcion': descripcion,
        },
    )

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
