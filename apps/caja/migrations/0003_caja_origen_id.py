"""
Identidad estable de `Caja` para el sync (CAJA-008).

El cloud resolvia la caja por `(nombre, sucursal)`, y `nombre` es un CharField
mutable y sin unicidad. Renombrar una caja entre la apertura y el cierre partia
el turno en dos: el movimiento no encontraba el turno viejo y el cierre creaba
por fallback otro turno bajo la caja nueva. El portal quedaba mostrando una
caja eternamente abierta y otra cerrada con el mismo efectivo.

Se hace en TRES pasos porque un campo unico con default no se puede agregar de
una sobre filas existentes: `uuid.uuid4` se evaluaria una sola vez y todas las
cajas quedarian con el MISMO valor, violando la unicidad.

  1. Agregar la columna nullable y sin unicidad.
  2. Poblar cada fila con su propio UUID.
  3. Recien ahi imponer NOT NULL + UNIQUE.
"""
import uuid

from django.db import migrations, models


def poblar_origen_id(apps, schema_editor):
    Caja = apps.get_model('caja', 'Caja')
    db = schema_editor.connection.alias

    for caja in Caja.objects.using(db).filter(origen_id__isnull=True):
        # Un UUID por fila: no se puede usar un default de columna.
        Caja.objects.using(db).filter(pk=caja.pk).update(origen_id=uuid.uuid4())


def sin_reversa(apps, schema_editor):
    """Al revertir se elimina la columna; no hay nada que restaurar."""


class Migration(migrations.Migration):

    dependencies = [
        ('caja', '0002_caja_sucursal'),
    ]

    operations = [
        migrations.AddField(
            model_name='caja',
            name='origen_id',
            field=models.UUIDField(
                null=True,
                editable=False,
                verbose_name='Identidad de sync',
                help_text='Identidad estable de esta caja entre sucursal y cloud.',
            ),
        ),
        migrations.RunPython(poblar_origen_id, sin_reversa),
        migrations.AlterField(
            model_name='caja',
            name='origen_id',
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                unique=True,
                verbose_name='Identidad de sync',
                help_text='Identidad estable de esta caja entre sucursal y cloud.',
            ),
        ),
    ]
