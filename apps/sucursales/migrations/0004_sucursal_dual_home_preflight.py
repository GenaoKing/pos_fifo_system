"""Impide que una FK de auditoria corra sobre una Sucursal no materializada."""

from django.db import migrations


def exigir_tabla_sucursal(apps, schema_editor):
    """El caso heredado se repara con un comando, nunca desde esta migracion."""
    Sucursal = apps.get_model('sucursales', 'Sucursal')
    tablas = set(schema_editor.connection.introspection.table_names())
    if Sucursal._meta.db_table not in tablas:
        raise RuntimeError(
            'sucursales esta registrada como aplicada pero su tabla no existe. '
            'No se materializa automaticamente. Ejecute primero '
            'reparar_sucursales_dual_home --dry-run sobre esta base y luego '
            'repita migrate.'
        )


def sin_reversa(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('sucursales', '0003_sucursal_negocio'),
    ]

    # No agregamos run_before contra auditoria.0002: esa migracion ya puede
    # figurar aplicada en una base heredada y Django la consideraria aplicada
    # antes de una dependencia nueva. La cadena limpia ya tiene el borde
    # auditoria.0002 -> sucursales.0001; el preflight de los comandos corta el
    # caso fantasma antes de que se ejecute cualquier migracion.

    operations = [
        migrations.RunPython(exigir_tabla_sucursal, sin_reversa),
    ]
