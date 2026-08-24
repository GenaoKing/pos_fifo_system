"""
Data migration: permisos `productos.ver` (ya existia en el catalogo) +
`productos.fotografiar` (nuevo) para el rol Cajero de sistema.

Abre el portal cloud a la cajera (BUG-G, docs/BUGS.md): puede entrar a ver el
catalogo y subir fotos de producto desde el celular, no crear/editar precios
ni categorias.

- Rol Cajero de sistema: + los dos permisos. Sin esto, una instalacion viva
  se queda sin ninguna cajera con acceso, porque `seed.crear_roles_default`
  solo fija permisos cuando el rol SE CREA (apps/permisos/seed.py) y ese rol
  ya existe en cada negocio.
- Rol Administrador: ya tiene todo el catalogo desde su propio seed; no hace
  falta tocarlo.

Idempotente.
"""
from django.db import migrations

PERMISOS = ['productos.ver', 'productos.fotografiar']


def aplicar(apps, schema_editor):
    Permiso = apps.get_model('permisos', 'Permiso')
    Rol = apps.get_model('permisos', 'Rol')

    from apps.permisos.catalogo import sembrar_catalogo

    sembrar_catalogo(Permiso)

    permisos = list(Permiso.objects.filter(codigo__in=PERMISOS))
    if not permisos:
        return

    for rol in Rol.objects.filter(es_sistema=True, slug='cajero'):
        rol.permisos.add(*permisos)


def revertir(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('permisos', '0007_permiso_autorizar_descuento'),
    ]

    operations = [
        migrations.RunPython(aplicar, revertir),
    ]
